"""The wtype keycode slot plan (desktop-e2e T2): exact argv for `type` and `key`.

wtype 0.4 numbers its keymap in order of first use (entry i is evdev keycode i+1, xkb i+9), shared
by -k/-p/-P; text after `--` gets new entries matched by character, never the -p ones. Chromium
reads KeyboardEvent.code from the keycode and VS Code dispatches shortcuts on it, so the plan
releases (-p) one keysym per keycode from 1 up: special keys on their real US keycode, characters
on character keycodes, inert private-use fillers elsewhere; everything is then typed with -k.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_input import slots  # noqa: E402

PRELUDE = ["-k", "Shift_L", "-s", "250"]
_XKB_FROM_NAME = slots._xkb_keysym_from_name  # the real one, before NoXkbTest patches it
SHIFT_L = 0xFFE1
# Keydown codes Chromium/VS Code must never see for a typed character (evdev numbers).
NON_CHAR = {1, 14, 15, 28, 29, 42, 54, 56, 58, *range(59, 71), 87, 88, 96, 97, 100,
            *range(102, 112), 125, 126, 127, *range(183, 195)}


def releases(by_code: dict[int, int]) -> list[str]:
    """-p args for keycodes 1..max: the given keysym, else the private-use filler of that code."""
    used = set(by_code.values())
    out: list[str] = []
    for code in range(1, max(by_code) + 1):
        ks = by_code.get(code)
        if ks is None:
            ks = 0x100E000 + code
            while ks in used:
                ks += 0x100
        out += ["-p", hex(ks)]
    return out


def typed(text: str) -> list[str]:
    return [x for ch in text for x in ("-k", hex(slots.keysym_of_char(ch)))]


@unittest.skipUnless(slots.xkb_available(), "libxkbcommon not available")
class PlanTest(unittest.TestCase):
    def test_hola_mundo(self) -> None:
        by_code = {35: 0x68, 24: 0x6F, 38: 0x6C, 30: 0x61, 57: 0x20, 50: 0x6D, 22: 0x75, 49: 0x6E,
                   32: 0x64, 42: SHIFT_L}
        self.assertEqual(slots.type_argvs("hola mundo"),
                         [["wtype", *releases(by_code), *PRELUDE, "-d", "8", *typed("hola mundo")]])

    def test_l27_sentence_with_legacy_keysyms(self) -> None:
        text = "Hola mundo, este es un texto de prueba. ’ — …"
        by_code = {35: 0x48, 24: 0x6F, 38: 0x6C, 30: 0x61, 57: 0x20, 50: 0x6D, 22: 0x75, 49: 0x6E,
                   32: 0x64, 51: 0x2C, 18: 0x65, 31: 0x73, 20: 0x74, 45: 0x78, 25: 0x70, 19: 0x72,
                   48: 0x62, 52: 0x2E, 42: SHIFT_L,
                   2: 0xAD1, 3: 0xAA9, 4: 0xAAE}  # ’ — … on the first free character keycodes
        self.assertEqual(slots.type_argvs(text),
                         [["wtype", *releases(by_code), *PRELUDE, "-d", "8", *typed(text)]])

    def test_vim_clear(self) -> None:
        # Escape on 1, g/d on their keys; G (shift+g, typed as the capital) on a free character key.
        by_code = {1: 0xFF1B, 34: 0x67, 32: 0x64, 2: 0x47, 42: SHIFT_L}
        args = ["-k", "Escape", "-s", "300", "-k", "g", "-k", "g", "-k", "d", "G"]
        self.assertEqual(slots.key_argv(args),
                         ["wtype", *releases(by_code), *PRELUDE,
                          "-k", "Escape", "-s", "300", "-k", "g", "-k", "g", "-k", "d", "-k", "0x47"])

    def test_ctrl_l(self) -> None:
        args = ["-M", "ctrl", "-k", "l", "-m", "ctrl"]
        self.assertEqual(slots.key_argv(args),
                         ["wtype", *releases({38: 0x6C, 42: SHIFT_L}), *PRELUDE, *args])

    def test_ctrl_a_delete(self) -> None:
        args = ["-M", "ctrl", "-k", "a", "-m", "ctrl", "-k", "Delete"]
        self.assertEqual(slots.key_argv(args),
                         ["wtype", *releases({30: 0x61, 111: 0xFFFF, 42: SHIFT_L}), *PRELUDE, *args])

    def test_shortcut_letter_wins_its_real_keycode(self) -> None:
        # "shift+a ctrl+a": the capital is typed first, but ctrl+a must stay on KeyA (30).
        argv = slots.key_argv(["A", "-M", "ctrl", "-k", "a", "-m", "ctrl"])
        reg = argv[1:argv.index("-k")]
        self.assertEqual(reg[2 * 29 + 1], "0x61")  # keycode 30
        self.assertEqual(reg[2 * 1 + 1], "0x41")   # keycode 2, the first free character key

    def test_return_and_tab_on_their_real_keycodes(self) -> None:
        [argv] = slots.type_argvs("a\tb\nc")
        reg = argv[1:argv.index("-k")]
        self.assertEqual(reg[2 * 14 + 1], "0xff09")  # Tab, keycode 15
        self.assertEqual(reg[2 * 27 + 1], "0xff0d")  # Return, keycode 28

    def test_text_never_on_special_keycodes(self) -> None:
        text = "".join(chr(c) for c in range(0x21, 0x7F)) + "áéíóúñüÁÉÍÓÚÑÜ¿¡’—…€ "
        argvs = slots.type_argvs(text)
        self.assertGreater(len(argvs), 1)  # more distinct characters than character keycodes
        out = ""
        for argv in argvs:
            reg = argv[1:argv.index("-k")]
            keysyms = [int(reg[i + 1], 16) for i in range(0, len(reg), 2)]
            self.assertLessEqual(len(keysyms), 127)
            self.assertEqual(len(set(keysyms)), len(keysyms))  # one keysym per keycode
            typed_ks = [int(argv[i + 1], 16) for i in range(argv.index("-d") + 2, len(argv), 2)]
            for ks in typed_ks:
                code = keysyms.index(ks) + 1
                self.assertNotIn(code, NON_CHAR, hex(ks))
            out += "".join(chr(ks) if ks < 0x100 else "\0" for ks in typed_ks)
        self.assertEqual(len(out), len(text))
        self.assertEqual("".join(a for a in out if a != "\0"),
                         "".join(a for a in text if ord(a) < 0x100))

    def test_private_use_text_does_not_clash_with_fillers(self) -> None:
        [argv] = slots.type_argvs("")
        reg = argv[1:argv.index("-k")]
        self.assertEqual(len(set(reg[1::2])), len(reg) // 2)

    def test_keysyms_match_libxkbcommon(self) -> None:
        # wtype maps characters with xkb_utf32_to_keysym, which picks legacy keysyms for many
        # characters agents write (’ — … €); 0x1000000+cp would be a different key (L28).
        self.assertEqual([slots.keysym_of_char(c) for c in "’—…€\n\t"],
                         [0xAD1, 0xAA9, 0xAAE, 0x20AC, 0xFF0D, 0xFF09])

    def test_plans_once_per_new_character(self) -> None:
        # The plan only changes when a new distinct keysym appears: no O(n²) replanning.
        with mock.patch.object(slots, "plan", wraps=slots.plan) as spy:
            [argv] = slots.type_argvs("ab" * 1000)
        self.assertLessEqual(spy.call_count, 3)
        self.assertEqual(argv, slots.type_argvs("ab")[0][:-4] + typed("ab" * 1000))

    def test_unknown_key(self) -> None:
        with self.assertRaises(ValueError):
            slots.key_argv(["-k", "NoSuchKeysym"])


class NoXkbTest(unittest.TestCase):
    """Without libxkbcommon `key` degrades like `type`: a built-in table of the names parse_keys
    produces (letters, digits, aliases, F-keys, common specials); anything else is refused."""

    def setUp(self) -> None:
        for name in ("_xkb_keysym_from_name", "_xkb_utf32_to_keysym"):
            p = mock.patch.object(slots, name, None)
            p.start()
            self.addCleanup(p.stop)

    def test_known_names(self) -> None:
        from jarvis_input import checks
        cases = {"a": 0x61, "Z": 0x7A, "7": 0x37, "Escape": 0xFF1B, "escape": 0xFF1B,
                 "Return": 0xFF0D, "KP_Enter": 0xFF8D, "Tab": 0xFF09, "BackSpace": 0xFF08,
                 "Delete": 0xFFFF, "Insert": 0xFF63, "Home": 0xFF50, "End": 0xFF57,
                 "Page_Up": 0xFF55, "Page_Down": 0xFF56, "Up": 0xFF52, "Down": 0xFF54,
                 "Left": 0xFF51, "Right": 0xFF53, "space": 0x20, "F1": 0xFFBE, "F12": 0xFFC9,
                 "F24": 0xFFD5, "Print": 0xFF61, "Menu": 0xFF67, "minus": 0x2D, "slash": 0x2F}
        for name, ks in cases.items():
            self.assertEqual(slots.keysym_of_name(name), ks, name)
        argv = slots.key_argv(checks.parse_keys("ctrl+a Delete Escape g shift+g F5"))
        self.assertEqual(argv[0], "wtype")
        self.assertIn("0xffff", argv)

    def test_unknown_name_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "libxkbcommon"):
            slots.keysym_of_name("XF86AudioMute")

    def test_type_still_works(self) -> None:
        self.assertEqual(len(slots.type_argvs("hola")), 1)

    @unittest.skipUnless(slots.xkb_available(), "libxkbcommon not available")
    def test_table_agrees_with_libxkbcommon(self) -> None:
        for name, ks in slots.FALLBACK_NAMES.items():
            for variant in (name, name.lower(), name.upper()):
                self.assertEqual(_XKB_FROM_NAME(variant.encode(), 1), ks, variant)


if __name__ == "__main__":
    unittest.main()
