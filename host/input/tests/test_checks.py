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

    # L17 (b): typed text with the Vim clear asks, in any app (fail-safe heuristic).
    def test_typed_vim_clear(self) -> None:
        for text in ["ggdG", "Escape ggdG", ":%d", ":%d\n", "x:%dx"]:
            for app_id in ["org.gnome.TextEditor", "code", "brave-browser"]:
                with self.subTest(text=text, app_id=app_id):
                    self.assertEqual(reasons(app_id, "Notas", text=text), [self.CLEAR])
        for text in ["gg dG", "ggdg", "%d:", ": %d", "hola", "g g d G"]:
            with self.subTest(text=text):
                self.assertEqual(reasons(text=text), [])
        # the same Vim ex clear as keys
        self.assertEqual(reasons(keys="Escape colon percent d Return"), [self.CLEAR])
        self.assertEqual(reasons(keys="shift+semicolon shift+5 d Return"), [self.CLEAR])

    # L17 (c): in a browser a tab switch followed by Return may land on web chat: it asks.
    def test_tab_switch_then_return_in_a_browser(self) -> None:
        for keys in ["ctrl+Tab Return", "ctrl+shift+Tab Return", "ctrl+Page_Down Return",
                     "ctrl+pageup Return", "ctrl+2 Return", "alt+9 Return", "ctrl+Tab a b ctrl+Return",
                     "ctrl+1 KP_Enter"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons("brave-browser", "recetas - Buscar con Google", keys=keys), [self.SEND])
        for keys in ["Return ctrl+Tab", "ctrl+Tab", "ctrl+2", "Tab Return", "2 Return", "shift+Tab Return"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons("brave-browser", "recetas - Buscar con Google", keys=keys), [])
        self.assertEqual(reasons(keys="ctrl+Tab Return"), [])  # not a browser

    # L17 (d): equivalents of the same actions.
    def test_tab_then_space_or_return_sends_in_chat(self) -> None:
        for keys in ["Tab space", "Tab Tab space", "shift+Tab space", "Tab Return", "Tab a space"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons("discord", "Discord", keys=keys), [self.SEND])
                self.assertEqual(reasons("brave-browser", "WhatsApp", keys=keys), [self.SEND])
        self.assertEqual(reasons("signal", "Signal", text="hola\t "), [self.SEND])
        for keys in ["space Tab", "Tab", "space"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons("discord", "Discord", keys=keys), [])
        self.assertEqual(reasons(keys="Tab space"), [])  # a plain editor
        self.assertEqual(reasons("brave-browser", "recetas", keys="Tab space"), [])

    def test_more_clear_all_equivalents(self) -> None:
        for keys in ["ctrl+a KP_Delete", "ctrl+Home ctrl+shift+End Delete", "ctrl+Home ctrl+shift+End BackSpace",
                     "ctrl+Home ctrl+shift+End ctrl+x", "ctrl+End ctrl+shift+Home BackSpace",
                     "ctrl+Home ctrl+shift+End KP_Delete", "ctrl+a x", "ctrl+a space"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [self.CLEAR])
        for keys in ["KP_Delete", "ctrl+Home Delete", "ctrl+shift+End Delete", "ctrl+Home shift+End Delete",
                     "ctrl+a ctrl+c", "ctrl+a Escape"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [])

    def test_more_close_equivalents(self) -> None:
        for keys in ["ctrl+F4", "super+w", "super+shift+e", "super+shift+E", "ctrl+alt+Delete", "super+q"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [self.CLOSE])
        for keys in ["ctrl+F5", "super+e", "super+1", "ctrl+Delete", "alt+Delete"]:
            with self.subTest(keys=keys):
                self.assertEqual(reasons(keys=keys), [])


def after(*steps: tuple[str, str], app_id: str = "org.gnome.TextEditor") -> tuple[list[str], checks.KeyTail]:
    """Run steps ("keys"|"text", value) through the cross-call tail; the reasons of the last one."""
    tail = checks.KeyTail()
    got: list[str] = []
    for kind, value in steps:
        kw = {"keys": checks.parse_keys(value)} if kind == "keys" else {"text": value}
        got = checks.confirm_reasons(app_id, "Notas", tail=tail, **kw)
        tail = checks.next_tail(tail, **kw)
    return got, tail


class CrossCallTest(unittest.TestCase):
    """L17 (a): clear-all is matched against the tail of the previous call plus the current one."""

    CLEAR = "borrar todo el contenido"

    def test_select_all_then_delete_in_the_next_call(self) -> None:
        for first, second in [("ctrl+a", "Delete"), ("ctrl+a", "BackSpace"), ("ctrl+a", "ctrl+x"),
                              ("ctrl+a", "KP_Delete"), ("ctrl+a Right", "Delete"), ("ctrl+Home", "ctrl+shift+End Delete"),
                              ("ctrl+Home ctrl+shift+End", "BackSpace"), ("Escape g g", "d shift+g"),
                              ("g", "g d shift+g"), ("g g d", "shift+g"), ("Escape colon", "percent d Return")]:
            with self.subTest(first=first, second=second):
                self.assertEqual(after(("keys", first), ("keys", second))[0], [self.CLEAR])

    def test_typing_over_a_pending_select_all(self) -> None:
        self.assertEqual(after(("keys", "ctrl+a"), ("text", "hola"))[0], [self.CLEAR])
        self.assertEqual(after(("keys", "ctrl+Home ctrl+shift+End"), ("text", "x"))[0], [self.CLEAR])
        self.assertEqual(after(("text", "gg"), ("keys", "d shift+g"))[0], [self.CLEAR])

    def test_a_finished_clear_does_not_ask_again(self) -> None:
        for first, second in [("ctrl+a Delete", "Delete"), ("ctrl+a Delete", "x"), ("ctrl+a ctrl+x", "BackSpace"),
                              ("Escape g g d shift+g", "i"), ("Escape g g d shift+g", "d shift+g"),
                              ("ctrl+a x", "Delete")]:
            with self.subTest(first=first, second=second):
                self.assertEqual(after(("keys", first), ("keys", second))[0], [])
        self.assertEqual(after(("keys", "ctrl+a"), ("text", "hola"), ("text", "chau"))[0], [])
        self.assertEqual(after(("keys", "ctrl+a"), ("text", "hola"), ("keys", "Delete"))[0], [])
        self.assertEqual(after(("keys", "g"), ("keys", "d shift+g"))[0], [])
        self.assertEqual(after(("text", "hola"), ("keys", "Delete"))[0], [])

    def test_tail_is_bounded(self) -> None:
        _, tail = after(("text", "a" * 2000))
        self.assertLessEqual(len(tail.recent), checks.TAIL_COMBOS)
        self.assertLessEqual(checks.TAIL_COMBOS, 8)

    def test_tails_per_window_and_expiry(self) -> None:
        now = [100.0]
        tails = checks.KeyTails(ttl=60, clock=lambda: now[0])
        _, pending = after(("keys", "ctrl+a"))
        self.assertEqual(tails.get(7), checks.KeyTail())
        tails.record(7, pending)
        self.assertEqual(tails.get(7), pending)
        self.assertEqual(tails.get(8), checks.KeyTail())  # another window has its own (empty) tail
        now[0] += 59
        self.assertEqual(tails.get(7), pending)
        now[0] += 2
        self.assertEqual(tails.get(7), checks.KeyTail())  # forgotten after the ttl
        tails.record(7, pending)
        tails.record(8, checks.KeyTail(select_all=False))
        self.assertEqual(tails.get(7), pending)  # T16: an action on another window keeps it

    def test_tails_are_bounded_lru(self) -> None:
        now = [100.0]
        tails = checks.KeyTails(ttl=60, clock=lambda: now[0])
        _, pending = after(("keys", "ctrl+a"))
        for wid in range(1, checks.TAIL_WINDOWS + 1):
            tails.record(wid, pending)
        tails.record(1, pending)  # window 1 is now the most recent; 2 is the least recent
        tails.record(100, pending)
        self.assertEqual(tails.get(2), checks.KeyTail())  # evicted
        for wid in [1, 100, *range(3, checks.TAIL_WINDOWS + 1)]:
            self.assertEqual(tails.get(wid), pending, wid)
        self.assertLessEqual(checks.TAIL_WINDOWS, 8)

    def test_tails_expire_per_entry(self) -> None:
        now = [100.0]
        tails = checks.KeyTails(ttl=60, clock=lambda: now[0])
        _, pending = after(("keys", "ctrl+a"))
        tails.record(7, pending)
        now[0] += 30
        tails.record(8, pending)
        now[0] += 31
        self.assertEqual(tails.get(7), checks.KeyTail())
        self.assertEqual(tails.get(8), pending)

    def test_failed_run_keeps_a_pending_select_all(self) -> None:
        _, pending = after(("keys", "ctrl+a"))
        tail = checks.next_tail(pending, text="hola", failed=True)  # maybe nothing was typed
        self.assertEqual(checks.confirm_reasons("org.gnome.TextEditor", "Notas", keys=["-k", "Delete"],
                                                tail=tail), [self.CLEAR])
        tail = checks.next_tail(checks.KeyTail(), keys=checks.parse_keys("ctrl+a"), failed=True)
        self.assertTrue(tail.select_all)  # maybe it was pressed


class ClearOverSelectAllTest(unittest.TestCase):
    """T16: paste, Return and Tab over a pending select-all replace everything: they ask."""

    CLEAR = "borrar todo el contenido"

    def test_asks(self) -> None:
        for second in ["ctrl+v", "ctrl+shift+v", "shift+Insert", "Return", "KP_Enter", "shift+Return", "Tab"]:
            with self.subTest(second=second):
                self.assertEqual(after(("keys", "ctrl+a"), ("keys", second))[0], [self.CLEAR])
                self.assertEqual(after(("keys", "ctrl+a " + second))[0], [self.CLEAR])
        for text in ["\n", "\t"]:
            with self.subTest(text=text):
                self.assertEqual(after(("keys", "ctrl+a"), ("text", text))[0], [self.CLEAR])
        self.assertEqual(after(("keys", "ctrl+a ctrl+c"), ("keys", "ctrl+v"))[0], [self.CLEAR])
        self.assertEqual(after(("keys", "ctrl+Home ctrl+shift+End"), ("keys", "ctrl+v"))[0], [self.CLEAR])

    def test_does_not_ask(self) -> None:
        for second in ["ctrl+c", "ctrl+Insert", "Right", "Home", "shift+Tab", "ctrl+Return", "Escape"]:
            with self.subTest(second=second):
                self.assertEqual(after(("keys", "ctrl+a"), ("keys", second))[0], [])
        for keys in ["ctrl+v", "shift+Insert", "Return", "Tab"]:  # nothing selected
            with self.subTest(keys=keys):
                self.assertEqual(after(("keys", keys))[0], [])
        self.assertEqual(after(("keys", "ctrl+a ctrl+v"), ("keys", "Return"))[0], [])  # consumed


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
