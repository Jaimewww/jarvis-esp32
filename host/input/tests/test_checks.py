"""Pure checks of jarvis-input (S13, S12 (3)): no sockets, no processes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_input import checks  # noqa: E402
from jarvis_input.checks import FocusedWindow  # noqa: E402


def win(app_id: str = "org.gnome.TextEditor", title: str = "Untitled", id: int = 7) -> FocusedWindow:
    return FocusedWindow(id=id, app_id=app_id, title=title)


class ParseKeysTest(unittest.TestCase):
    def test_single_and_combos(self) -> None:
        self.assertEqual(checks.parse_keys("Return"), ["-k", "Return"])
        self.assertEqual(checks.parse_keys("enter"), ["-k", "Return"])
        self.assertEqual(checks.parse_keys("ctrl+l"), ["-M", "ctrl", "-k", "l", "-m", "ctrl"])
        self.assertEqual(
            checks.parse_keys("ctrl+shift+t"),
            ["-M", "ctrl", "-M", "shift", "-k", "t", "-m", "shift", "-m", "ctrl"],
        )
        self.assertEqual(checks.parse_keys("super+1"), ["-M", "logo", "-k", "1", "-m", "logo"])
        self.assertEqual(
            checks.parse_keys("ctrl+a Delete"),
            ["-M", "ctrl", "-k", "a", "-m", "ctrl", "-k", "Delete"],
        )
        self.assertEqual(checks.parse_keys("f5"), ["-k", "F5"])
        self.assertEqual(checks.parse_keys("esc"), ["-k", "Escape"])

    def test_shift_letter_sends_the_capital(self) -> None:
        # wtype resolves -k names case-insensitively, so "-k G" and "-M shift -k g" both reached
        # VS Code (Vim) as "g" (L23, L24). Plain shift+letter is sent as the capital as text, which
        # wtype types with its own keysym; shortcuts with other modifiers keep the key.
        self.assertEqual(checks.parse_keys("shift+g"), ["G"])
        self.assertEqual(checks.parse_keys("shift+G"), ["G"])
        self.assertEqual(
            checks.parse_keys("Escape g g d shift+g"),
            ["-k", "Escape", "-k", "g", "-k", "g", "-k", "d", "G"],
        )
        self.assertEqual(checks.parse_keys("shift+Tab"), ["-M", "shift", "-k", "Tab", "-m", "shift"])
        self.assertEqual(checks.parse_keys("g"), ["-k", "g"])  # no shift: unchanged

    def test_rejects_bad_input(self) -> None:
        for bad in ["", "   ", "ctrl+", "+a", "hyper+a", "ctrl+ctrl+a", "ctrl", "ctrl+shift",
                    "-k", "a;b", "--help", "x" * 40, " ".join(["a"] * 21), "ctrl++"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                checks.parse_keys(bad)
        with self.assertRaises(ValueError):
            checks.parse_keys(123)  # type: ignore[arg-type]

    def test_presses_return(self) -> None:
        self.assertTrue(checks.presses_return(checks.parse_keys("Return")))
        self.assertTrue(checks.presses_return(checks.parse_keys("ctrl+Return")))
        self.assertTrue(checks.presses_return(checks.parse_keys("KP_Enter")))
        self.assertTrue(checks.presses_return(checks.parse_keys("ctrl+a enter")))
        self.assertFalse(checks.presses_return(checks.parse_keys("ctrl+l Tab")))
        for keys in ["KP_ENTER", "kp_enter", "ctrl+RETURN", "iso_enter", "LineFeed", "3270_Enter"]:
            self.assertTrue(checks.presses_return(checks.parse_keys(keys)), keys)

    def test_refuses_raw_keysym_numbers(self) -> None:
        # xkb resolves "0xff0d" to Return (and "0xff8d" to KP_Enter) behind the name check.
        for keys in ["0xff0d", "0XFF8D", "ctrl+0xff0d", "a 0x61", "0x0", "U000D", "uff0d"]:
            with self.assertRaises(ValueError, msg=keys):
                checks.parse_keys(keys)
        self.assertEqual(checks.parse_keys("0 9 Up"), ["-k", "0", "-k", "9", "-k", "Up"])


class TextTest(unittest.TestCase):
    def test_validate_text(self) -> None:
        self.assertIsNone(checks.validate_text("hola\nmundo\tok"))
        self.assertIsNone(checks.validate_text("á" * 2000))
        self.assertIsNotNone(checks.validate_text(""))
        self.assertIsNotNone(checks.validate_text("a" * 2001))
        self.assertIsNotNone(checks.validate_text("a\x00b"))
        self.assertIsNotNone(checks.validate_text("a\rb"))
        self.assertIsNotNone(checks.validate_text("a\x1bb"))
        self.assertIsNotNone(checks.validate_text("a\x7fb"))
        self.assertIsNotNone(checks.validate_text(None))  # type: ignore[arg-type]


class RiskyTest(unittest.TestCase):
    def test_stems(self) -> None:
        cases = {
            "envía el correo": ["envi"],
            "Envíe ya": ["envi"],
            "PAGAR ahora": ["pag"],
            "compra esto": ["compr"],
            "eliminar archivo": ["elimin"],
            "borrá todo": ["borr"],
            "Submit form": ["submit"],
            "sending": ["send"],
            "PayPal": ["pay"],
            "buy now": ["buy"],
            "Deleted": ["delet"],
            "send and pay": ["send", "pay"],
            # L23: "Vaciar el documento" emptied a document without a confirmation card.
            "Vaciar el documento": ["vaci"],
            "vacía todo": ["vaci"],
            "clear the file": ["clear"],
            "wipe it": ["wipe"],
        }
        for text, stems in cases.items():
            with self.subTest(text=text):
                self.assertEqual(checks.risky_stems(text), stems)

    def test_not_risky(self) -> None:
        for text in ["hola mundo", "resend", "un repago", "subir archivo", "pa ge", ""]:
            with self.subTest(text=text):
                self.assertEqual(checks.risky_stems(text), [])

    def test_normalization(self) -> None:
        self.assertEqual(checks.risky_stems("\uff53\uff45\uff4e\uff44"), ["send"])  # fullwidth (NFKC)
        self.assertEqual(checks.risky_stems("se\u200bnd"), ["send"])  # zero-width space
        self.assertEqual(checks.risky_stems("de\u00adlete"), ["delet"])  # soft hyphen
        self.assertEqual(checks.risky_stems("e\u0301nviar"), ["envi"])  # combining accent

    def test_lookalikes(self) -> None:
        self.assertEqual(checks.lookalike_stems("s\u0435nd"), ["send"])  # Cyrillic e
        self.assertEqual(checks.lookalike_stems("\u0440ay"), ["pay"])  # Cyrillic er
        self.assertEqual(checks.lookalike_stems("b\u03c5y"), ["buy"])  # Greek upsilon
        self.assertEqual(checks.lookalike_stems("send"), [])  # plain: risky, not a lookalike
        self.assertEqual(checks.lookalike_stems("\u043f\u0440\u0438\u0432\u0435\u0442"), [])

    def test_multiple_texts(self) -> None:
        self.assertEqual(checks.risky_stems("hola", "enviar mensaje"), ["envi"])


class FocusTest(unittest.TestCase):
    def test_parse(self) -> None:
        w = checks.parse_focused_window('{"id": 3, "app_id": "brave-browser", "title": "X", "pid": 1}')
        self.assertEqual(w, FocusedWindow(id=3, app_id="brave-browser", title="X"))
        self.assertIsNone(checks.parse_focused_window("null"))
        w = checks.parse_focused_window('{"id": 4, "app_id": null, "title": null}')
        self.assertEqual(w, FocusedWindow(id=4, app_id="", title=""))

    def test_parse_errors(self) -> None:
        for bad in ["", "nope", "[]", '{"app_id": "x"}', '{"id": true}', '{"id": "3"}', "3"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                checks.parse_focused_window(bad)

    def test_blocklist(self) -> None:
        for app_id in ["org.keepassxc.KeePassXC", "Bitwarden", "1Password", "gcr-prompter",
                       "org.gnome.Settings", "polkit-gnome-authentication-agent-1", "pinentry-qt",
                       "org.kde.ksshaskpass", "xdg-desktop-portal-gnome", "com.protonpass.Proton"]:
            with self.subTest(app_id=app_id):
                self.assertIsNotNone(checks.blocked_reason(win(app_id), screenshot=False))
                self.assertIsNotNone(checks.blocked_reason(win(app_id), screenshot=True))
        for title in ["Authentication Required", "Se requiere autenticación", "KeePassXC - db"]:
            with self.subTest(title=title):
                self.assertIsNotNone(checks.blocked_reason(win("x", title), screenshot=True))

    def test_terminals_block_input_only(self) -> None:
        for app_id in ["foot", "footclient", "kitty", "Alacritty", "com.mitchellh.ghostty",
                       "org.wezfurlong.wezterm", "org.gnome.Console", "org.kde.konsole", "xterm"]:
            with self.subTest(app_id=app_id):
                self.assertIsNotNone(checks.blocked_reason(win(app_id), screenshot=False))
                self.assertIsNone(checks.blocked_reason(win(app_id), screenshot=True))

    def test_allowed(self) -> None:
        for app_id in ["org.gnome.TextEditor", "brave-browser", "firefox", "spotify"]:
            with self.subTest(app_id=app_id):
                self.assertIsNone(checks.blocked_reason(win(app_id), screenshot=False))

    def test_confirm_apps(self) -> None:
        for app_id in ["brave-browser", "firefox", "chromium", "google-chrome", "org.mozilla.Thunderbird",
                       "org.telegram.desktop", "signal", "discord", "Slack", "whatsapp-for-linux",
                       "im.riot.Riot", "Element", "zen", "app.zen_browser.zen", "librewolf"]:
            with self.subTest(app_id=app_id):
                self.assertTrue(checks.needs_return_confirm(app_id))
        for app_id in ["org.gnome.TextEditor", "spotify", "", "code"]:
            with self.subTest(app_id=app_id):
                self.assertFalse(checks.needs_return_confirm(app_id))


class OpenValidationTest(unittest.TestCase):
    def test_desktop_id(self) -> None:
        for ok in ["brave-browser", "org.gnome.TextEditor", "spotify.desktop", "com.spotify.Client", "a_b"]:
            with self.subTest(ok=ok):
                self.assertIsNone(checks.validate_desktop_id(ok))
        for bad in ["", "-x", ".hidden", "../x", "a/b", "a b", "a;b", "a\nb", "x" * 256, "ñandú", 3, None]:
            with self.subTest(bad=bad):
                self.assertIsNotNone(checks.validate_desktop_id(bad))  # type: ignore[arg-type]

    def test_url(self) -> None:
        for ok in ["https://open.spotify.com", "http://localhost:8080/x?y=1#z", "https://ejemplo.com/ñ"]:
            with self.subTest(ok=ok):
                self.assertIsNone(checks.validate_url(ok))
        for bad in ["", "file:///etc/passwd", "javascript:alert(1)", "ftp://x", "https://", "http:///x",
                    "https://a b", "https://a\nb", "-https://x", "HTTPS//x", "x" * 3000, "data:text/html,1",
                    "https://x" + "a" * 3000, 7, None]:
            with self.subTest(bad=bad):
                self.assertIsNotNone(checks.validate_url(bad))  # type: ignore[arg-type]


class RateLimiterTest(unittest.TestCase):
    def test_sliding_window(self) -> None:
        now = [0.0]
        take = checks.RateLimiter(10, 1.0, clock=lambda: now[0])
        self.assertTrue(all(take() for _ in range(10)))
        self.assertFalse(take())
        now[0] = 0.5
        self.assertFalse(take())
        now[0] = 1.01
        self.assertTrue(take())


class LogPreviewTest(unittest.TestCase):
    def test_preview_never_full(self) -> None:
        secret = "mi clave secreta larguísima"
        preview = checks.preview(secret)
        self.assertNotIn(secret, preview)
        self.assertIn(str(len(secret)), preview)


if __name__ == "__main__":
    unittest.main()
