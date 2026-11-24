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


class LookalikeTest(unittest.TestCase):
    """Lookalike letters that turn a word into a risky one are still denied outright (S10a)."""

    def test_lookalikes(self) -> None:
        self.assertEqual(checks.lookalike_stems("s\u0435nd"), ["send"])  # Cyrillic e
        self.assertEqual(checks.lookalike_stems("\u0440ay"), ["pay"])  # Cyrillic er
        self.assertEqual(checks.lookalike_stems("b\u03c5y"), ["buy"])  # Greek upsilon
        self.assertEqual(checks.lookalike_stems("b\u043errar"), ["borr"])  # Cyrillic o
        self.assertEqual(checks.lookalike_stems("s\u0435\u200bnd"), ["send"])  # plus zero-width space
        self.assertEqual(checks.lookalike_stems("\uff42\u043errar"), ["borr"])  # fullwidth + Cyrillic
        self.assertEqual(checks.lookalike_stems("hola", "\u0440agar"), ["pag"])  # any of the texts

    def test_plain_words_are_not_lookalikes(self) -> None:
        for text in ["send", "borrar todo", "enviar", "pagar", "hola mundo", "",
                     "\u043f\u0440\u0438\u0432\u0435\u0442"]:  # real Russian: no Latin risky word
            with self.subTest(text=text):
                self.assertEqual(checks.lookalike_stems(text), [])

    def test_word_gating_is_gone(self) -> None:
        # S10a (2): words in the text or the intent never ask by themselves.
        self.assertFalse(hasattr(checks, "risky_stems"))


GMAIL = "Recibidos (3) - diego@example.com - Gmail"


def reasons(app_id: str = "org.gnome.TextEditor", title: str = "Notas", *, keys: str | None = None,
            text: str | None = None) -> list[str]:
    return checks.confirm_reasons(app_id, title, keys=checks.parse_keys(keys) if keys is not None else None,
                                  text=text)


class ConfirmRulesTest(unittest.TestCase):
    """S10a: the rule table of what asks the user first, by the action, never by words."""

    SEND, CLEAR, CLOSE = "enviar en un chat o correo", "borrar todo el contenido", "cerrar la ventana"

    def test_chat_and_mail_apps(self) -> None:
        for app_id in ["org.mozilla.Thunderbird", "org.telegram.desktop", "signal", "discord", "Slack",
                       "whatsapp-for-linux", "im.riot.Riot", "Element", "org.gnome.Geary", "vesktop",
                       "com.github.eneshecan.WhatsAppForLinux", "teams-for-linux"]:
            with self.subTest(app_id=app_id):
                self.assertTrue(checks.is_chat_or_mail(app_id, "Algo"))
        for app_id in ["org.gnome.TextEditor", "spotify", "", "code", "brave-browser", "firefox",
                       "chromium", "google-chrome", "zen", "app.zen_browser.zen", "librewolf"]:
            with self.subTest(app_id=app_id):
                self.assertFalse(checks.is_chat_or_mail(app_id, "recetas - Buscar con Google"))

    def test_web_chat_and_mail_in_a_browser_by_title(self) -> None:
        for title in ["WhatsApp", "(2) WhatsApp Business", "Telegram Web", GMAIL, "Correo: Diego - Outlook",
                      "#general | Servidor - Discord", "Messenger | Facebook", "general (Canal) - Slack",
                      "TELEGRAM", "Whatsápp", "Inbox | diego | Proton Mail", "Chat | Microsoft Teams"]:
            for app_id in ["brave-browser", "firefox", "chromium", "zen", "org.mozilla.firefox"]:
                with self.subTest(app_id=app_id, title=title):
                    self.assertTrue(checks.is_chat_or_mail(app_id, title))
        for title in ["recetas - Buscar con Google", "Formulario de inscripción", "", "Inbox",
                      "GitHub - pull request"]:
            with self.subTest(title=title):
                self.assertFalse(checks.is_chat_or_mail("brave-browser", title))
        # A title only counts in a browser: an editor with a file called WhatsApp is no chat.
        self.assertFalse(checks.is_chat_or_mail("org.gnome.TextEditor", "WhatsApp.md"))

    def test_return_asks_only_in_chat_and_mail(self) -> None:
        self.assertEqual(reasons("brave-browser", "recetas - Buscar con Google", keys="Return"), [])
        self.assertEqual(reasons("brave-browser", "WhatsApp", keys="Return"), [self.SEND])
        self.assertEqual(reasons("brave-browser", GMAIL, keys="ctrl+Return"), [self.SEND])
        self.assertEqual(reasons("org.telegram.desktop", "Telegram", keys="Return"), [self.SEND])
        self.assertEqual(reasons("org.mozilla.Thunderbird", "Redactar", keys="ctrl+Return"), [self.SEND])
        self.assertEqual(reasons(keys="Return"), [])  # a plain editor
        self.assertEqual(reasons(keys="ctrl+Return"), [])

    def test_any_return_name_anywhere_in_the_sequence(self) -> None:
        for keys in ["KP_Enter", "kp_ENTER", "ctrl+a enter", "Tab Tab Return", "shift+Return", "iso_enter",
                     "LineFeed", "3270_Enter", "h o l a ctrl+RETURN"]:
            with self.subTest(keys=keys):
                self.assertIn(self.SEND, reasons("discord", "Discord", keys=keys))
        self.assertEqual(reasons("discord", "Discord", keys="ctrl+l Tab a"), [])

    def test_newline_in_typed_text_is_return(self) -> None:
        self.assertEqual(reasons("signal", "Signal", text="hola\n"), [self.SEND])
        self.assertEqual(reasons("brave-browser", "WhatsApp", text="hola\nchau"), [self.SEND])
        self.assertEqual(reasons("signal", "Signal", text="hola"), [])
        self.assertEqual(reasons("brave-browser", "recetas", text="hola\n"), [])
        self.assertEqual(reasons(text="línea 1\nlínea 2\n"), [])

    def test_words_never_ask(self) -> None:
        for text in ["borrar todo", "enviar el correo", "pagar la factura", "BORRAR", "delete all", "send it",
                     "comprar ya", "vaciar"]:
            with self.subTest(text=text):
                self.assertEqual(reasons(text=text), [])
                self.assertEqual(reasons("signal", "Signal", text=text), [])  # no newline: no send
        self.assertEqual(reasons(keys="a"), [])  # the intent "sin borrar" is never looked at
        self.assertEqual(reasons(keys="ctrl+v"), [])

    def test_clear_all_content(self) -> None:
        for keys in ["ctrl+a Delete", "ctrl+a BackSpace", "ctrl+a ctrl+x", "ctrl+A del", "ctrl+a backspace",
                     "ctrl+a Escape Delete", "Escape g g d shift+g", "g g d shift+G", "Escape g g d shift+g i"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [self.CLEAR])
        for keys in ["Delete", "BackSpace", "ctrl+x", "ctrl+a", "Delete ctrl+a", "ctrl+a ctrl+c",
                     "g g", "d shift+g", "g g d g", "ctrl+l Delete"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [])

    def test_close_window_or_app(self) -> None:
        for keys in ["alt+F4", "alt+f4", "ctrl+w", "ctrl+q", "ctrl+W", "ctrl+shift+w", "Escape ctrl+q"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [self.CLOSE])
        for keys in ["F4", "w", "q", "shift+w", "alt+w", "ctrl+e"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [])

    def test_several_reasons_in_order_once_each(self) -> None:
        self.assertEqual(reasons("discord", "Discord", keys="ctrl+a Delete Return ctrl+w Return"),
                         [self.SEND, self.CLEAR, self.CLOSE])


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


class MediaArgvTest(unittest.TestCase):
    """T15: the closed media/volume set (C4) maps to a fixed argv; nothing else is accepted."""

    def test_fixed_argv_per_action(self) -> None:
        sink = "@DEFAULT_AUDIO_SINK@"
        self.assertEqual(checks.media_argv("play_pause"), ["playerctl", "play-pause"])
        self.assertEqual(checks.media_argv("next"), ["playerctl", "next"])
        self.assertEqual(checks.media_argv("previous"), ["playerctl", "previous"])
        self.assertEqual(checks.media_argv("volume_up"), ["wpctl", "set-volume", "-l", "1.0", sink, "5%+"])
        self.assertEqual(checks.media_argv("volume_down"), ["wpctl", "set-volume", sink, "5%-"])
        self.assertEqual(checks.media_argv("mute"), ["wpctl", "set-mute", sink, "1"])
        self.assertEqual(checks.media_argv("unmute"), ["wpctl", "set-mute", sink, "0"])
        self.assertEqual(set(checks.MEDIA_ACTIONS),
                         {"play_pause", "next", "previous", "volume_up", "volume_down", "mute", "unmute"})

    def test_argv_is_a_fresh_copy(self) -> None:
        checks.media_argv("next").append("--evil")
        self.assertEqual(checks.media_argv("next"), ["playerctl", "next"])

    def test_rejects_anything_else(self) -> None:
        for bad in [None, 3, "", "PLAY_PAUSE", "play-pause", "volume_up ", "stop", "volume_up;rm",
                    ["next"], {"action": "next"}, "x" * 500]:
            with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                checks.media_argv(bad)
            self.assertIn("play_pause", str(ctx.exception))
            self.assertLess(len(str(ctx.exception)), 300)


class LogPreviewTest(unittest.TestCase):
    def test_preview_never_full(self) -> None:
        secret = "mi clave secreta larguísima"
        preview = checks.preview(secret)
        self.assertNotIn(secret, preview)
        self.assertIn(str(len(secret)), preview)


if __name__ == "__main__":
    unittest.main()
