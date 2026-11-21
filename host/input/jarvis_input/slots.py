"""The wtype argv for `type` and `key`: a keycode slot plan (desktop-e2e T2).

How wtype 0.4 numbers keys (main.c, checked against the source): all argv is parsed before any
key is sent and one keymap is uploaded. Every new keysym gets the next keymap entry, in order of
first use; entry i is evdev keycode i+1 (xkb keycode i+9). `-k`, `-p` and `-P` share the entries
and match them by keysym; text (after `--` or a bare argument) matches by character only, so it
never reuses a `-p` entry and always appends after them. `-M`/`-m` only send modifier state and
take no keycode.

Chromium derives KeyboardEvent.code from the keycode and VS Code dispatches shortcuts on it, so a
character on keycode 14/15 acts as Backspace/Tab (desktop-e2e L2), on 1 as Escape, on 29/42 as a
modifier. The plan therefore releases (`-p`, which apps ignore) one keysym per keycode from 1 up
to the highest one used, so each lands where we want: special keys on their real US keycode,
characters (and shortcut letters) on character keycodes, and an inert private-use filler on every
other keycode. Everything is then typed with `-k <keysym>`, which reuses those entries; there is
no `--` text, so every argument after the prelude is generated (`0x…` or a checked key name).
"""

from __future__ import annotations

import ctypes
import ctypes.util

# The first key is a harmless Shift tap that carries the keymap, then a sleep while the app
# applies it: Chromium/Electron drop keys that arrive sooner ("wtype hola" typed "la", L19).
PRELUDE = ("-k", "Shift_L", "-s", "250")
KEY_DELAY_MS = "8"

_ESCAPE, _TAB, _RETURN, _SHIFT_L = 0xFF1B, 0xFF09, 0xFF0D, 0xFFE1

# Keysyms with a fixed US keycode (evdev) that is not a character key.
SPECIAL: dict[int, int] = {
    _ESCAPE: 1, 0xFF08: 14, _TAB: 15, _RETURN: 28,  # Escape BackSpace Tab Return
    0xFFE3: 29, 0xFFE1: 42, 0xFFE2: 54, 0xFFE9: 56, 0xFFE5: 58,  # Control_L Shift_L/R Alt_L Caps
    0xFF7F: 69, 0xFF14: 70, 0xFF8D: 96, 0xFFE4: 97, 0xFF61: 99,  # Num/Scroll_Lock KP_Enter Ctrl_R Print
    0xFFEA: 100, 0xFE03: 100, 0xFF50: 102, 0xFF52: 103, 0xFF55: 104,  # Alt_R AltGr Home Up Prior
    0xFF51: 105, 0xFF53: 106, 0xFF57: 107, 0xFF54: 108, 0xFF56: 109,  # Left Right End Down Next
    0xFF63: 110, 0xFFFF: 111, 0xFF13: 119, 0xFFEB: 125, 0xFFEC: 126, 0xFF67: 127,  # … Menu
    **{0xFFBE + i: 59 + i for i in range(10)},  # F1-F10
    0xFFC8: 87, 0xFFC9: 88,  # F11 F12
    **{0xFFCA + i: 183 + i for i in range(12)},  # F13-F24
}

# Keycodes of US character keys, in the order free ones are handed out: main block, Space, the
# ISO key, then the numpad (never NumpadEnter). Chromium reports these as character codes.
CHAR_CODES: tuple[int, ...] = (*range(2, 14), *range(16, 28), *range(30, 42), *range(43, 54), 57,
                               86, *range(71, 84), 55, 98, 117, 121)


def _us_chars() -> dict[int, int]:
    rows = {2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*", 10: "9(",
            11: "0)", 12: "-_", 13: "=+", 26: "[{", 27: "]}", 39: ";:", 40: "'\"", 41: "`~",
            43: "\\|", 51: ",<", 52: ".>", 53: "/?", 57: " "}
    for code, ch in zip(range(16, 26), "qwertyuiop"):
        rows[code] = ch + ch.upper()
    for code, ch in zip(range(30, 39), "asdfghjkl"):
        rows[code] = ch + ch.upper()
    for code, ch in zip(range(44, 51), "zxcvbnm"):
        rows[code] = ch + ch.upper()
    return {ord(ch): code for code, chars in rows.items() for ch in chars}


# Keysyms of US-layout characters (Latin-1 keysym = code point) -> their own key's keycode.
US_CHAR: dict[int, int] = _us_chars()

_FILLER = 0x100E000  # Unicode private-use keysyms: they type nothing anybody binds


def _load_xkb():  # type: ignore[no-untyped-def]
    name = ctypes.util.find_library("xkbcommon")
    try:
        lib = ctypes.CDLL(name or "libxkbcommon.so.0")
        utf32, from_name = lib.xkb_utf32_to_keysym, lib.xkb_keysym_from_name
    except (OSError, AttributeError):
        return None, None
    utf32.argtypes, utf32.restype = [ctypes.c_uint32], ctypes.c_uint32
    from_name.argtypes, from_name.restype = [ctypes.c_char_p, ctypes.c_int], ctypes.c_uint32
    return utf32, from_name


_xkb_utf32_to_keysym, _xkb_keysym_from_name = _load_xkb()


def xkb_available() -> bool:
    return _xkb_keysym_from_name is not None


def keysym_of_char(ch: str) -> int:
    """The keysym wtype types for one character: its own remaps, then xkb_utf32_to_keysym
    (legacy keysyms for ’ — … € and many more, L28); without libxkbcommon, the Unicode keysym."""
    cp = ord(ch)
    if ch == "\n":
        return _RETURN
    if ch == "\t":
        return _TAB
    if _xkb_utf32_to_keysym is not None:
        ks = _xkb_utf32_to_keysym(cp)
        if ks:
            return int(ks)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp  # Latin-1 keysyms equal the code point
    return 0x1000000 + cp


def keysym_of_name(name: str) -> int:
    """The keysym wtype resolves `-k name` to (case-insensitive, like wtype). ValueError if none."""
    if _xkb_keysym_from_name is None:
        raise ValueError("falta libxkbcommon para resolver teclas")
    ks = int(_xkb_keysym_from_name(name.encode(), 1))  # XKB_KEYSYM_CASE_INSENSITIVE
    if not ks:
        raise ValueError(f"tecla desconocida: {name}")
    return ks


def plan(keysyms: list[int], shortcuts: frozenset[int] | set[int] = frozenset()) -> list[int] | None:
    """Keysym per keycode 1..N (index + 1), or None when the characters do not fit.

    Special keys get their real keycode; shortcut keysyms (pressed with a modifier) and then the
    rest, in first-use order, get their own US key if free, else the next free character key."""
    by_code: dict[int, int] = {}
    order = [k for k in keysyms if k in shortcuts] + [k for k in keysyms if k not in shortcuts]
    rest: list[int] = []
    for ks in dict.fromkeys(order):
        code = SPECIAL.get(ks)
        if code is None or code in by_code:
            code = US_CHAR.get(ks)
            if code is None or code in by_code:
                rest.append(ks)
                continue
        by_code[code] = ks
    free = (c for c in CHAR_CODES if c not in by_code)
    for ks in rest:
        code = next(free, None)
        if code is None:
            return None
        by_code[code] = ks
    used = set(by_code.values())
    out: list[int] = []
    for code in range(1, max(by_code) + 1):
        ks = by_code.get(code)
        if ks is None:
            ks = _FILLER + code
            while ks in used:
                ks += 0x100
        out.append(ks)
    return out


def _releases(slots: list[int]) -> list[str]:
    return [x for ks in slots for x in ("-p", hex(ks))]


def type_argvs(text: str) -> list[list[str]]:
    """wtype argvs that type `text`; more than one only when it has more distinct characters
    than there are character keycodes (each run uploads its own keymap and needs the prelude)."""
    argvs: list[list[str]] = []
    chunk: list[int] = []
    for ks in [keysym_of_char(ch) for ch in text] + [None]:
        if ks is not None and plan([_SHIFT_L, *chunk, ks]) is not None:
            chunk.append(ks)
            continue
        if chunk:
            slots = plan([_SHIFT_L, *chunk])
            assert slots is not None
            argvs.append(["wtype", *_releases(slots), *PRELUDE, "-d", KEY_DELAY_MS,
                          *[x for k in chunk for x in ("-k", hex(k))]])
        chunk = [] if ks is None else [ks]
    return argvs


def key_argv(args: list[str]) -> list[str]:
    """wtype argv for parse_keys output (plus pauses): `-k name`, `-M/-m mod`, `-s ms`, and bare
    text (a capital for shift+letter), which becomes `-k <keysym>` so it lands on a planned key."""
    out: list[str] = []
    keysyms: list[int] = [_SHIFT_L]
    shortcuts: set[int] = set()
    held = 0
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-M", "-m", "-s"):
            held += {"-M": 1, "-m": -1, "-s": 0}[a]
            out += [a, args[i + 1]]
            i += 2
            continue
        if a == "-k":
            ks = keysym_of_name(args[i + 1])
            keysyms.append(ks)
            if held:
                shortcuts.add(ks)
            out += ["-k", args[i + 1]]
            i += 2
            continue
        for ch in a:
            ks = keysym_of_char(ch)
            keysyms.append(ks)
            out += ["-k", hex(ks)]
        i += 1
    slots = plan(keysyms, shortcuts)
    if slots is None:
        raise ValueError("demasiadas teclas distintas")
    return ["wtype", *_releases(slots), *PRELUDE, *out]
