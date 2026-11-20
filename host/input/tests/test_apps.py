"""S14: visible `.desktop` entries (`apps`) and `niri msg -j windows` parsing. Pure, temp dirs only."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_input import checks  # noqa: E402


def entry(name: str, **extra: str) -> str:
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}", "Exec=/bin/false"]
    lines += [f"{k.replace('_', '[', 1) + ']' if '_' in k else k}={v}" for k, v in extra.items()]
    return "\n".join(lines) + "\n"


class DataDirsTest(unittest.TestCase):
    def test_defaults_and_env(self) -> None:
        home = Path("/home/u")
        dirs = checks.desktop_data_dirs({"HOME": str(home)}, home=home, flatpak_system=Path("/none"))
        self.assertEqual(dirs, [home / ".local/share", Path("/usr/local/share"), Path("/usr/share")])
        dirs = checks.desktop_data_dirs({"XDG_DATA_HOME": "/d/h", "XDG_DATA_DIRS": "/a:relative::/b:/a"},
                                        home=home, flatpak_system=Path("/none"))
        self.assertEqual(dirs, [Path("/d/h"), Path("/a"), Path("/b")])

    def test_flatpak_exports_appended_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            user = root / "h" / "flatpak" / "exports" / "share"
            system = root / "sys" / "exports" / "share"
            user.mkdir(parents=True)
            system.mkdir(parents=True)
            dirs = checks.desktop_data_dirs({"XDG_DATA_HOME": str(root / "h"), "XDG_DATA_DIRS": "/usr/share"},
                                            home=root, flatpak_system=system)
            self.assertEqual(dirs, [root / "h", Path("/usr/share"), user, system])
            dirs = checks.desktop_data_dirs({"XDG_DATA_HOME": str(root / "h"), "XDG_DATA_DIRS": str(system)},
                                            home=root, flatpak_system=system)
            self.assertEqual(dirs, [root / "h", system, user])


class ListAppsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.high = self.root / "high"
        self.low = self.root / "low"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, base: Path, rel: str, text: str) -> None:
        p = base / "applications" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def apps(self, query: str | None = None, cap: int = 500) -> tuple[list[dict[str, str]], bool]:
        return checks.list_apps([self.high, self.root / "missing", self.low], query=query, cap=cap)

    def test_ids_names_filters_and_sort(self) -> None:
        self.put(self.low, "dev.duxlabs.shell.notepad.desktop",
                 entry("Notepad", Name_es="Bloc de notas", GenericName="Text editor", Keywords="notes;memo;"))
        self.put(self.low, "kde/org.kde.dolphin.desktop", entry("Dolphin"))
        self.put(self.low, "brave-browser.desktop", entry("Brave"))
        self.put(self.low, "hidden.desktop", entry("Hidden", Hidden="true"))
        self.put(self.low, "nodisplay.desktop", entry("NoDisplay", NoDisplay="true"))
        self.put(self.low, "link.desktop", "[Desktop Entry]\nType=Link\nName=Link\nURL=https://x\n")
        self.put(self.low, "noname.desktop", "[Desktop Entry]\nType=Application\nExec=x\n")
        self.put(self.low, "other-group.desktop", "[Desktop Action new]\nName=Nope\n[Desktop Entry]\n"
                 "Type=Application\nName=Real\n")
        self.put(self.low, "bad id.desktop", entry("Spaces"))
        self.put(self.low, "readme.txt", entry("Txt"))
        apps, truncated = self.apps()
        self.assertFalse(truncated)
        self.assertEqual(apps, [
            {"id": "dev.duxlabs.shell.notepad", "name": "Bloc de notas"},
            {"id": "brave-browser", "name": "Brave"},
            {"id": "kde-org.kde.dolphin", "name": "Dolphin"},
            {"id": "other-group", "name": "Real"},
        ])

    def test_precedence_and_hidden_masks(self) -> None:
        self.put(self.high, "a.desktop", entry("Mine"))
        self.put(self.low, "a.desktop", entry("System"))
        self.put(self.high, "b.desktop", entry("B", Hidden="true"))
        self.put(self.low, "b.desktop", entry("B system"))
        self.assertEqual(self.apps()[0], [{"id": "a", "name": "Mine"}])

    def test_query_case_and_accent_insensitive(self) -> None:
        self.put(self.low, "dev.duxlabs.shell.notepad.desktop",
                 entry("Notepad", Name_es="Bloc de notas", Keywords_es="apuntes;"))
        self.put(self.low, "org.gnome.Calculator.desktop", entry("Calculator", Name_es="Calculadora",
                                                                 GenericName="Calculadora científica"))
        self.put(self.low, "brave-browser.desktop", entry("Brave", Keywords="web;internet;"))
        names = lambda q: [a["id"] for a in self.apps(q)[0]]  # noqa: E731
        self.assertEqual(names("NOTAS"), ["dev.duxlabs.shell.notepad"])
        self.assertEqual(names("notepad"), ["dev.duxlabs.shell.notepad"])  # default Name
        self.assertEqual(names("Apuntes"), ["dev.duxlabs.shell.notepad"])  # Keywords[es]
        self.assertEqual(names("cientifica"), ["org.gnome.Calculator"])  # GenericName, accent
        self.assertEqual(names("internet"), ["brave-browser"])
        self.assertEqual(names("gnome.calc"), ["org.gnome.Calculator"])  # id
        self.assertEqual(names("zzz"), [])
        self.assertEqual(len(names("")), 3)

    def test_cap_and_safety(self) -> None:
        for i in range(12):
            self.put(self.low, f"app{i:02}.desktop", entry(f"App {i:02}"))
        apps, truncated = self.apps(cap=5)
        self.assertTrue(truncated)
        self.assertEqual([a["id"] for a in apps], [f"app{i:02}" for i in range(5)])
        self.put(self.low, "huge.desktop", entry("Huge") + "X-Pad=" + "a" * (300 * 1024) + "\n")
        (self.low / "applications" / "binary.desktop").write_bytes(b"\xff\xfe[Desktop Entry]\x00\n")
        long_name = "N" * 500
        self.put(self.low, "long.desktop", entry(long_name))
        apps, _ = self.apps(cap=100)
        ids = [a["id"] for a in apps]
        self.assertNotIn("huge", ids)
        self.assertNotIn("binary", ids)
        self.assertEqual(len(next(a for a in apps if a["id"] == "long")["name"]), 200)


class ParseWindowsTest(unittest.TestCase):
    def test_parse(self) -> None:
        out = json.dumps([
            {"id": 3, "app_id": "foot", "title": "jarvis", "is_focused": True, "workspace_id": 1},
            {"id": 9, "app_id": None, "title": "t" * 300, "is_focused": False},
        ])
        wins = checks.parse_windows(out)
        self.assertEqual([(w.id, w.app_id, w.focused) for w in wins], [(3, "foot", True), (9, "", False)])
        self.assertEqual(len(wins[1].title), 300)  # parse keeps it; the server truncates the output

    def test_rejects(self) -> None:
        for bad in ["{}", "null", "[1]", '[{"title":"x"}]', '[{"id":true}]', "nope"]:
            with self.assertRaises(ValueError, msg=bad):
                checks.parse_windows(bad)


if __name__ == "__main__":
    unittest.main()
