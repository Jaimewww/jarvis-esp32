"""S15: `open` resolves a desktop entry and refuses code the sandboxed daemon could rewrite.

Pure: temp dirs only, nothing is executed. A fake $HOME and a fake fixed PATH live side by side.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_input import launch  # noqa: E402


class ParseExecTest(unittest.TestCase):
    def test_plain_and_quoted(self) -> None:
        self.assertEqual(launch.parse_exec("brave %U"), ["brave", "%U"])
        self.assertEqual(launch.parse_exec('  a   "b c"  d  '), ["a", "b c", "d"])
        self.assertEqual(launch.parse_exec(r'sh -c "echo \"hi\" \$x \\ \`"'),
                         ["sh", "-c", 'echo "hi" $x \\ `'])
        self.assertEqual(launch.parse_exec('a ""'), ["a", ""])

    def test_bad_quoting(self) -> None:
        for bad in ['a "b', "", "   "]:
            with self.assertRaises(ValueError, msg=bad):
                launch.parse_exec(bad)

    def test_field_codes(self) -> None:
        argv = ["app", "%U", "--x=%f", "%i", "%c", "%k", "100%%", "%d"]
        self.assertEqual(launch.expand_field_codes(argv, None), ["app", "--x=", "100%"])
        self.assertEqual(launch.expand_field_codes(["b", "%u", "%%u"], "https://x"),
                         ["b", "https://x", "%u"])
        # no URL field code: the URL is appended
        self.assertEqual(launch.expand_field_codes(["b"], "https://x"), ["b", "https://x"])
        # only one URL even with several codes
        self.assertEqual(launch.expand_field_codes(["b", "%U", "%F"], "https://x"), ["b", "https://x"])


class ResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home" / "u"
        self.bin = self.root / "usr" / "bin"
        self.sys_data = self.root / "usr" / "share"
        self.flatpak = self.root / "var" / "lib" / "flatpak" / "exports" / "share"
        self.home_data = self.home / ".local" / "share"
        for d in (self.home / ".local" / "bin", self.bin, self.sys_data, self.home_data):
            d.mkdir(parents=True)
        for name in ("brave", "gnome-text-editor", "flatpak", "sh", "bash", "env", "python3", "nohup",
                     "kitty", "foot"):
            self.exe(self.bin / name)
        self.exe(self.home / ".local" / "bin" / "evil")
        (self.bin / "jarvis-link").symlink_to(self.home / ".local" / "bin" / "evil")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def exe(p: Path) -> None:
        p.write_text("#!/bin/false\n")
        p.chmod(0o755)

    def put(self, base: Path, app_id: str, exec_: str | None, **extra: str) -> Path:
        lines = ["[Desktop Entry]", "Type=Application", f"Name={app_id}"]
        if exec_ is not None:
            lines.append(f"Exec={exec_}")
        lines += [f"{k}={v}" for k, v in extra.items()]
        p = base / "applications" / f"{app_id}.desktop"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n")
        return p

    def resolve(self, app_id: str, url: str | None = None) -> launch.Launch:
        return launch.resolve(app_id, [self.home_data, self.sys_data, self.flatpak], home=self.home,
                              search_path=str(self.bin), url=url, extra_untrusted=(),
                              is_writable=lambda p: False)

    def refused(self, app_id: str, needle: str, url: str | None = None) -> None:
        with self.assertRaises(launch.Refused) as cm:
            self.resolve(app_id, url)
        self.assertIn(needle, str(cm.exception))

    # allowed -------------------------------------------------------------
    def test_system_app_allowed(self) -> None:
        self.put(self.sys_data, "org.gnome.TextEditor", "gnome-text-editor %U")
        got = self.resolve("org.gnome.TextEditor")
        self.assertEqual(got.argv, ["gnome-text-editor"])
        self.assertEqual(got.executable, str(self.bin / "gnome-text-editor"))
        self.assertEqual(got.cwd, "/")
        self.assertEqual(got.desktop, self.sys_data / "applications" / "org.gnome.TextEditor.desktop")

    def test_absolute_exec_and_url(self) -> None:
        self.put(self.sys_data, "brave-browser", f"{self.bin}/brave --flag %U")
        got = self.resolve("brave-browser", url="https://example.com/home/u/x")
        self.assertEqual(got.argv, [f"{self.bin}/brave", "--flag", "https://example.com/home/u/x"])

    def test_url_handed_to_app_is_checked(self) -> None:
        # Review R3-url-unchecked: the URL spliced into argv must pass the same checks as Exec.
        self.put(self.sys_data, "brave-browser", f"{self.bin}/brave %U")
        for bad in ("--renderer-cmd-prefix=/home/u/x", "-x", "file:///home/u/evil.html",
                    "/home/u/evil.html", "javascript:alert(1)", "https://a.b/ x", ""):
            with self.subTest(url=bad):
                with self.assertRaises(launch.Refused):
                    self.resolve("brave-browser", url=bad)

    def test_flatpak_allowed(self) -> None:
        target = self.root / "var" / "lib" / "flatpak" / "app" / "com.brave.Browser.desktop"
        target.parent.mkdir(parents=True)
        target.write_text("[Desktop Entry]\nType=Application\nName=Brave\nExec=" + str(self.bin) +
                          "/flatpak run --branch=stable --command=brave --file-forwarding com.brave.Browser"
                          " @@u %U @@\n")
        (self.flatpak / "applications").mkdir(parents=True)
        (self.flatpak / "applications" / "com.brave.Browser.desktop").symlink_to(target)
        got = self.resolve("com.brave.Browser", url="https://a.b")
        self.assertEqual(got.argv[-4:], ["com.brave.Browser", "@@u", "https://a.b", "@@"])

    def test_shell_with_system_script_allowed(self) -> None:
        (self.root / "usr" / "lib").mkdir()
        self.exe(self.root / "usr" / "lib" / "app.sh")
        self.put(self.sys_data, "wrapped", f"sh {self.root}/usr/lib/app.sh --x")
        self.assertEqual(self.resolve("wrapped").argv[0], "sh")

    def test_env_wrapper_allowed(self) -> None:
        self.put(self.sys_data, "e", "env GDK_BACKEND=x11 brave -c conf -e x")  # flags of brave, not env
        self.assertEqual(self.resolve("e").argv, ["env", "GDK_BACKEND=x11", "brave", "-c", "conf", "-e", "x"])

    def test_path_key_outside_home(self) -> None:
        self.put(self.sys_data, "a", "brave", Path=str(self.root))
        self.assertEqual(self.resolve("a").cwd, str(self.root))
        self.put(self.sys_data, "b", "brave", Path=str(self.home))
        self.refused("b", "$HOME")

    # refused: where the entry or program lives ----------------------------
    def test_entry_in_home_refused_even_if_exec_is_system(self) -> None:
        self.put(self.home_data, "brave-browser", "brave %U")
        self.put(self.sys_data, "brave-browser", "brave %U")  # masked by the home one
        self.refused("brave-browser", "$HOME")

    def test_desktop_symlink_into_home_refused(self) -> None:
        real = self.put(self.home / "x", "linked", "brave")
        (self.sys_data / "applications").mkdir()
        (self.sys_data / "applications" / "linked.desktop").symlink_to(real)
        self.refused("linked", "$HOME")

    def test_exec_in_home_refused(self) -> None:
        self.put(self.sys_data, "a", f"{self.home}/.local/bin/evil")
        self.refused("a", "$HOME")
        self.put(self.sys_data, "b", "jarvis-link")  # /usr/bin symlink into ~/.local/bin
        self.refused("b", "$HOME")
        self.put(self.sys_data, "c", "~/.local/bin/evil")
        self.refused("c", "$HOME")

    def test_not_found(self) -> None:
        self.refused("missing", "No encontré")
        self.put(self.sys_data, "a", "evil")  # only in ~/.local/bin, not on the fixed PATH
        self.refused("a", "no está en")
        self.put(self.sys_data, "b", "relative/brave")
        self.refused("b", "ruta absoluta")
        self.put(self.sys_data, "c", None)
        self.refused("c", "Exec")
        self.put(self.sys_data, "d", 'brave "unterminated')
        self.refused("d", "Exec")

    def test_args_pointing_into_home_refused(self) -> None:
        for i, exec_ in enumerate([f"python3 {self.home}/x.py", f"env FOO=1 {self.home}/.local/bin/evil",
                                   f"brave --user-data-dir={self.home}/p", f"nohup {self.home}/a.AppImage",
                                   "brave ~/x"]):
            self.put(self.sys_data, f"a{i}", exec_)
            self.refused(f"a{i}", "$HOME")

    def test_inline_code_refused(self) -> None:
        for i, exec_ in enumerate(["sh -c 'x'", 'bash -lc "x"', "env sh -c x", "nohup bash -c x",
                                   "python3 -c x", "python3 -m mod", "env -S x", "env --split-string=x",
                                   "env -u X -S x", "env FOO=1 -iS x", "env FOO=1 sh -c x"]):
            self.put(self.sys_data, f"c{i}", exec_)
            self.refused(f"c{i}", "código")

    # refused: kind of entry -------------------------------------------------
    def test_terminal_refused(self) -> None:
        self.put(self.sys_data, "org.example.App", "brave", Terminal="true")
        self.refused("org.example.App", "terminal")
        self.put(self.sys_data, "kitty", "kitty")
        self.refused("kitty", "terminal")
        self.put(self.sys_data, "com.mitchellh.ghostty", "brave")
        self.refused("com.mitchellh.ghostty", "terminal")
        self.put(self.sys_data, "innocent", "foot --server")  # program is a terminal
        self.refused("innocent", "terminal")
        self.put(self.sys_data, "fp", "flatpak run com.mitchellh.ghostty")
        self.refused("fp", "terminal")

    def test_hidden_and_type_refused(self) -> None:
        self.put(self.sys_data, "h", "brave", Hidden="true")
        self.refused("h", "oculta")
        self.put(self.sys_data, "n", "brave", NoDisplay="true")
        self.refused("n", "oculta")
        p = self.put(self.sys_data, "l", "brave")
        p.write_text(p.read_text().replace("Type=Application", "Type=Link"))
        self.refused("l", "aplicación")

    def test_writable_and_untrusted_roots(self) -> None:
        self.put(self.sys_data, "a", "brave")
        with self.assertRaises(launch.Refused) as cm:
            launch.resolve("a", [self.sys_data], home=self.home, search_path=str(self.bin),
                           extra_untrusted=(), is_writable=lambda p: p == self.bin / "brave")
        self.assertIn("modificar", str(cm.exception))
        with self.assertRaises(launch.Refused):
            launch.resolve("a", [self.sys_data], home=self.home, search_path=str(self.bin),
                           extra_untrusted=(self.root / "usr",), is_writable=lambda p: False)

    def test_real_is_writable(self) -> None:
        self.assertTrue(launch.is_writable(self.bin / "brave"))  # ours, in a dir we own
        self.assertFalse(launch.is_writable(Path("/usr/bin/env")))


class LaunchEnvTest(unittest.TestCase):
    def test_env(self) -> None:
        env = launch.launch_env({"PATH": "/home/u/bin:/usr/bin", "LD_PRELOAD": "x", "LD_LIBRARY_PATH": "y",
                                 "LD_AUDIT": "z", "WAYLAND_DISPLAY": "wayland-1"})
        self.assertEqual(env, {"PATH": "/usr/local/bin:/usr/bin", "WAYLAND_DISPLAY": "wayland-1"})


class DefaultBrowserTest(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(launch.parse_default_handler("brave-browser.desktop\n"), "brave-browser")
        for bad in ["", "\n", "../x.desktop", "a b.desktop", "x.desktop\ny.desktop\n"]:
            self.assertIsNone(launch.parse_default_handler(bad), bad)


if __name__ == "__main__":
    unittest.main()
