"""Pure parts of the desktop E2E runner (jarvis_e2e); nothing here starts a compositor or a client."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis_e2e import cdp, desktop, run  # noqa: E402


class FrameTest(unittest.TestCase):
    def roundtrip(self, payload: bytes) -> None:
        frame = cdp.encode_frame(payload, mask=b"\x01\x02\x03\x04")
        fin, opcode, n, mask, pos = cdp.parse_header(frame)  # type: ignore[misc]
        self.assertTrue(fin)
        self.assertEqual(opcode, cdp.OP_TEXT)
        self.assertEqual(n, len(payload))
        self.assertEqual(mask, b"\x01\x02\x03\x04")
        body = frame[pos:]
        self.assertEqual(bytes(b ^ mask[i % 4] for i, b in enumerate(body)), payload)

    def test_lengths(self) -> None:
        for n in (0, 1, 125, 126, 65535, 65536, 70000):
            self.roundtrip(bytes(i % 251 for i in range(n)))

    def test_short_header_is_none(self) -> None:
        frame = cdp.encode_frame(b"x" * 300, mask=b"abcd")
        for cut in (0, 1, 3, 7):
            self.assertIsNone(cdp.parse_header(frame[:cut]))

    def test_unmasked_server_frame(self) -> None:
        self.assertEqual(cdp.parse_header(b"\x81\x05hello"), (True, 1, 5, None, 2))

    def test_bad_mask(self) -> None:
        with self.assertRaises(ValueError):
            cdp.encode_frame(b"x", mask=b"abc")

    def test_only_local_websocket(self) -> None:
        with self.assertRaises(cdp.CdpError):
            cdp.Cdp("ws://example.com:9222/devtools/page/x")

    def test_devtools_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertIsNone(cdp.devtools_port(d))
            (d / "DevToolsActivePort").write_text("41234\n/devtools/browser/abc\n")
            self.assertEqual(cdp.devtools_port(d), 41234)
            (d / "DevToolsActivePort").write_text("nope\n")
            self.assertIsNone(cdp.devtools_port(d))


class DesktopTest(unittest.TestCase):
    def which(self, have: set[str]):  # type: ignore[no-untyped-def]
        return lambda name: f"/usr/bin/{name}" if name in have else None

    def test_requirements(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-1"}
        ok = self.which({"niri", "wtype"})
        self.assertIsNone(desktop.missing_requirements(env, ok, "/usr/bin/brave"))
        self.assertIn("WAYLAND_DISPLAY", desktop.missing_requirements({}, ok, "/usr/bin/brave") or "")
        self.assertIn("wtype", desktop.missing_requirements(env, self.which({"niri"}), "b") or "")
        self.assertIn("niri", desktop.missing_requirements(env, self.which({"wtype"}), "b") or "")
        self.assertIn("browser", desktop.missing_requirements(env, ok, None) or "")

    def test_outer_wayland(self) -> None:
        self.assertEqual(desktop.outer_wayland({"WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": "/run/user/7"}),
                         "/run/user/7/wayland-1")
        self.assertEqual(desktop.outer_wayland({"WAYLAND_DISPLAY": "/x/w"}), "/x/w")

    def test_nested_sockets(self) -> None:
        d = Path("/r")
        self.assertIsNone(desktop.nested_sockets(d, ["wayland-1.lock"]))
        self.assertIsNone(desktop.nested_sockets(d, ["wayland-1", "wayland-1.lock"]))
        self.assertEqual(desktop.nested_sockets(d, ["wayland-1.lock", "niri.wayland-1.9.sock", "wayland-1"]),
                         (d / "wayland-1", d / "niri.wayland-1.9.sock"))

    def test_client_env_drops_the_user_session(self) -> None:
        base = {"DISPLAY": ":0", "NIRI_SOCKET": "/run/user/1/niri.sock", "WAYLAND_DISPLAY": "wayland-1",
                "HOME": "/h", "VSCODE_IPC_HOOK_CLI": "/x"}
        env = desktop.client_env(base, Path("/t/run"), Path("/t/run/wayland-1"), Path("/t/run/niri.s"))
        self.assertNotIn("DISPLAY", env)
        self.assertNotIn("VSCODE_IPC_HOOK_CLI", env)
        self.assertEqual(env["WAYLAND_DISPLAY"], "/t/run/wayland-1")
        self.assertEqual(env["NIRI_SOCKET"], "/t/run/niri.s")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/t/run")
        self.assertEqual(env["HOME"], "/h")


class RunTest(unittest.TestCase):
    def test_summarize(self) -> None:
        def P(s: str) -> run.Result:
            return run.Result("c", s)
        F = S = P
        self.assertEqual(run.summarize([P("PASS"), S("SKIP")])[1], run.EXIT_PASS)
        self.assertEqual(run.summarize([P("PASS"), F("FAIL")])[1], run.EXIT_FAIL)
        self.assertEqual(run.summarize([S("SKIP")])[1], run.EXIT_SKIP)
        self.assertEqual(run.summarize([])[1], run.EXIT_SKIP)
        self.assertEqual(run.summarize([P("PASS"), F("FAIL"), S("SKIP")])[0],
                         "summary: 1 passed, 1 failed, 1 skipped")

    def test_keydowns_skip_prelude_shift(self) -> None:
        events = [{"type": "keyup", "key": "Escape", "code": "Escape"},
                  {"type": "keydown", "key": "Shift", "code": "Digit1"},
                  {"type": "keydown", "key": "Escape", "code": "Escape"},
                  {"type": "keyup", "key": "Escape", "code": "Escape"},
                  {"type": "keydown", "key": "G", "code": "Digit2"}]
        self.assertEqual(run.keydowns(events), [("Escape", "Escape"), ("G", "Digit2")])

    def test_non_char_codes(self) -> None:
        downs = [("a", "KeyA"), ("1", "Numpad1"), (" ", "Space"), ("a", "Backspace"), (" ", "Tab"),
                 ("u", "ControlLeft"), ("ñ", ""), ("—", "ShiftLeft")]
        self.assertEqual(run.non_char_codes(downs), ["a/Backspace", " /Tab", "u/ControlLeft", "ñ/", "—/ShiftLeft"])

    def test_editor_text(self) -> None:
        self.assertEqual(run.editor_text([(19.0, "b c"), (0.0, "a")]), "a\nb c")
        self.assertEqual(run.editor_text([(0.0, "")]), "")

    def test_vim_mode(self) -> None:
        self.assertEqual(run.vim_mode(["Ln 1", "-- INSERT --"]), "INSERT")
        self.assertEqual(run.vim_mode(["-- NORMAL --"]), "NORMAL")
        self.assertIsNone(run.vim_mode(["Ln 1, Col 1"]))

    def test_assert_isolated(self) -> None:
        root = Path("/tmp/jarvis-e2e-x")
        good = {"WAYLAND_DISPLAY": "/tmp/jarvis-e2e-x/run/wayland-1",
                "NIRI_SOCKET": "/tmp/jarvis-e2e-x/run/niri.sock", "XDG_RUNTIME_DIR": "/tmp/jarvis-e2e-x/run"}
        run.assert_isolated(good, root)
        for key, value in [("WAYLAND_DISPLAY", "wayland-1"), ("NIRI_SOCKET", "/run/user/1000/niri.sock"),
                           ("XDG_RUNTIME_DIR", "/tmp/jarvis-e2e-xy/run"), ("DISPLAY", ":0")]:
            with self.assertRaises(desktop.SetupError, msg=key):
                run.assert_isolated({**good, key: value}, root)

    def test_find_browser_prefers_brave(self) -> None:
        self.assertEqual(run.find_browser(lambda n: f"/b/{n}" if n in ("chromium", "brave") else None), "/b/brave")
        self.assertIsNone(run.find_browser(lambda n: None))

    def test_skips_without_wayland(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
        p = subprocess.run([sys.executable, "-m", "jarvis_e2e"], cwd=ROOT, env=env, capture_output=True,
                           text=True, timeout=30, check=False)
        self.assertEqual(p.returncode, run.EXIT_SKIP, p.stdout + p.stderr)
        self.assertIn("SKIP: requirements", p.stdout)


if __name__ == "__main__":
    unittest.main()
