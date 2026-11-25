"""Pure checks of jarvis-input (S13, S12 (3)): no sockets, no processes, no I/O.

Ported from ``host/gentle-dot/extensions/pc-control-core.ts`` (key parsing, text validation,
blocklist, rate limit) and extended: lookalike letters that imitate risky words (denied), terminals
blocked for input, and the rule table of actions that ask the user first (S10a).
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
import unicodedata
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_TEXT_CHARS = 2000
MAX_INTENT_CHARS = 500
MAX_KEY_COMBOS = 20
MAX_URL_CHARS = 2048

# --- keys (port of parseKeys) ------------------------------------------------------------

_MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "altgr": "altgr",
    "super": "logo", "logo": "logo", "win": "logo", "meta": "logo", "mod": "logo",
}
_KEY_ALIASES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "space": "space", "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "insert": "Insert", "home": "Home", "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
}
# An xkb keysym name: never starts with "-", so wtype cannot read it as an option.
_KEYSYM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_]{0,31}")
_FKEY = re.compile(r"f([1-9]|1[0-9]|2[0-4])", re.IGNORECASE)
# xkb also resolves raw keysym numbers ("0xff0d" is Return) and Unicode names ("U000D"): refused,
# so the Return check below sees every name that can press Return.
_RAW_KEYSYM = re.compile(r"0x[0-9a-f]+|u[0-9a-f]+", re.IGNORECASE)
_RETURN_KEYS = {"return", "kp_enter", "iso_enter", "linefeed", "3270_enter"}


def parse_keys(keys: str) -> list[str]:
    """"ctrl+l", "super+1", "Return", "ctrl+a Delete" -> wtype argv (-M mod … -k key … -m mod).

    Raises ValueError on anything else.
    """
    if not isinstance(keys, str):
        raise ValueError("keys debe ser texto")
    combos = keys.split()
    if not combos:
        raise ValueError("keys vacío")
    if len(combos) > MAX_KEY_COMBOS:
        raise ValueError(f"keys admite como máximo {MAX_KEY_COMBOS} combinaciones")
    args: list[str] = []
    for combo in combos:
        parts = combo.split("+")
        if any(p == "" for p in parts):
            raise ValueError(f"combinación inválida: {combo}")
        key_part = parts.pop()
        mods: list[str] = []
        for p in parts:
            mod = _MODIFIERS.get(p.lower())
            if mod is None:
                raise ValueError(f"modificador desconocido: {p}")
            if mod in mods:
                raise ValueError(f"modificador repetido: {p}")
            mods.append(mod)
        if key_part.lower() in _MODIFIERS:
            raise ValueError(f"falta la tecla en: {combo}")
        key = _KEY_ALIASES.get(key_part.lower())
        if key is None:
            key = key_part.upper() if _FKEY.fullmatch(key_part) else key_part
        if not _KEYSYM.fullmatch(key) or _RAW_KEYSYM.fullmatch(key):
            raise ValueError(f"tecla inválida: {key_part}")
        if mods == ["shift"] and len(key) == 1 and "a" <= key.lower() <= "z":
            # wtype resolves -k names case-insensitively ("-k G" types "g", L24): send the capital
            # as a text argument, which wtype types with the capital keysym.
            args.append(key.upper())
            continue
        for m in mods:
            args += ["-M", m]
        args += ["-k", key]
        for m in reversed(mods):
            args += ["-m", m]
    return args


def presses_return(args: list[str]) -> bool:
    """Whether wtype key args from parse_keys press Return/Enter (with or without modifiers);
    names are matched case-insensitively, as wtype resolves them."""
    return any(a == "-k" and b.lower() in _RETURN_KEYS for a, b in zip(args, args[1:]))


# --- text ----------------------------------------------------------------------------------

_BAD_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def validate_text(text: object) -> str | None:
    """Why `text` cannot be typed, or None. Newlines and tabs are fine; other controls are not."""
    if not isinstance(text, str):
        return "text debe ser texto"
    if not text:
        return "text vacío"
    if len(text) > MAX_TEXT_CHARS:
        return f"text supera {MAX_TEXT_CHARS} caracteres"
    if _BAD_CONTROL.search(text):
        return "text tiene caracteres de control"
    return None


def preview(text: str) -> str:
    """For logs: the length and at most the first 3 characters, never the whole text."""
    head = text[: min(3, len(text) // 4)]
    return f"{len(text)} caracteres, empieza {head!r}…"


# --- lookalike words -----------------------------------------------------------------------

# Words are never a reason to ask (S10a); these stems only catch text or an intent that imitates
# them with Cyrillic/Greek lookalike letters, which is denied outright.
RISKY_STEMS = ("envi", "pag", "compr", "elimin", "borr", "vaci", "submit", "send", "pay", "buy",
               "delet", "clear", "wipe")

# Basic confusables: Cyrillic and Greek letters that look like Latin ones (lowercase, after casefold).
_CONFUSABLES = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s", "і": "i", "ї": "i", "ј": "j", "ԁ": "d",
    "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "ү": "y", "ɡ": "g", "ı": "i",
    "α": "a", "β": "b", "ε": "e", "η": "n", "ι": "i", "κ": "k", "μ": "u", "ν": "v", "ο": "o",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ω": "w", "ϲ": "c", "ζ": "z",
})
_WORD = re.compile(r"[^\W_]+")


def _base(text: str) -> str:
    """NFKC (fullwidth, ligatures), drop format chars (zero-width, soft hyphen), strip accents, casefold."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.casefold()


def lookalike_stems(*texts: str) -> list[str]:
    """Risky stems that only appear after mapping Cyrillic/Greek lookalikes to Latin (deny those)."""
    found: list[str] = []
    for text in texts:
        for word in _WORD.findall(_base(text)):
            mapped = word.translate(_CONFUSABLES)
            if mapped == word:
                continue
            for stem in RISKY_STEMS:
                if mapped.startswith(stem) and stem not in found:
                    found.append(stem)
    return found


# --- focused window ------------------------------------------------------------------------


@dataclass(frozen=True)
class FocusedWindow:
    id: int
    app_id: str
    title: str


def parse_focused_window(stdout: str) -> FocusedWindow | None:
    """`niri msg -j focused-window`: a window object, or None when nothing is focused. ValueError otherwise."""
    data = json.loads(stdout)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("niri: respuesta inesperada")
    wid = data.get("id")
    if not isinstance(wid, int) or isinstance(wid, bool):
        raise ValueError("niri: ventana sin id")

    def text(v: object) -> str:
        return v if isinstance(v, str) else ""

    return FocusedWindow(id=wid, app_id=text(data.get("app_id")), title=text(data.get("title")))


# app_id substrings (lowercase): password managers, system/security settings, auth prompts, portals.
SECRET_APP_IDS = (
    "keepassxc", "keepass", "bitwarden", "1password", "enpass", "lastpass", "dashlane", "protonpass",
    "proton.pass", "gopass", "qtpass", "passwordsafe", "world.secrets", "authenticator", "seahorse",
    "kwallet", "keyring", "gnome-control-center", "org.gnome.settings", "systemsettings", "kcmshell",
    "lxqt-config", "xfce4-settings", "nm-connection-editor", "polkit", "policykit", "gcr-prompter",
    "pinentry", "askpass", "xdg-desktop-portal", "gparted",
)
# Title substrings (lowercase) for prompts whose app_id varies.
SECRET_TITLES = (
    "keepassxc", "bitwarden", "1password", "authentication required", "autenticación requerida",
    "se requiere autenticación", "polkit", "pinentry", "unlock keyring", "desbloquear el depósito",
    "enter passphrase",
)
# Terminals: typing there runs commands outside the daemon's sandbox (and could drive the Jarvis
# TUI itself, e.g. "/control on"), so input is refused; screenshots of them are allowed.
TERMINAL_APP_ID_PARTS = (
    "terminal", "konsole", "wezterm", "ghostty", "alacritty", "kitty", "xterm", "rxvt", "tilix",
    "terminator", "ptyxis", "org.gnome.console", "blackbox", "terminology", "contour", "foot",
)
TERMINAL_APP_IDS = {"kgx", "st", "st-256color", "sakura", "rio", "warp", "dev.warp.warp", "wave"}


def _is_terminal(app_id: str) -> bool:
    return app_id in TERMINAL_APP_IDS or any(p in app_id for p in TERMINAL_APP_ID_PARTS)


def blocked_reason(window: FocusedWindow, *, screenshot: bool, subject: str = "La ventana enfocada") -> str | None:
    """Why the helper must not act on this window, or None. Screenshots skip the terminal rule."""
    app_id = window.app_id.casefold()
    title = window.title.casefold()
    label = f'"{window.app_id or "?"}": "{window.title[:200]}"'
    if any(p in app_id for p in SECRET_APP_IDS) or any(p in title for p in SECRET_TITLES):
        return (f"{subject} ({label}) está en la lista de bloqueo (contraseñas, ajustes o "
                "diálogos de seguridad); no se actúa sobre ella.")
    if not screenshot and _is_terminal(app_id):
        return (f"{subject} ({label}) es una terminal y está en la lista de bloqueo: "
                "escribir ahí ejecutaría órdenes fuera del agente.")
    return None


# --- windows (S14) -------------------------------------------------------------------------

MAX_WINDOWS = 500
MAX_TITLE_CHARS = 200


@dataclass(frozen=True)
class Window:
    id: int
    app_id: str
    title: str
    focused: bool

    def as_focused(self) -> FocusedWindow:
        return FocusedWindow(id=self.id, app_id=self.app_id, title=self.title)


def parse_windows(stdout: str) -> list[Window]:
    """`niri msg -j windows`: a list of window objects. ValueError on anything else."""
    data = json.loads(stdout)
    if not isinstance(data, list):
        raise ValueError("niri: respuesta inesperada")
    out: list[Window] = []
    for item in data[:MAX_WINDOWS]:
        if not isinstance(item, dict):
            raise ValueError("niri: ventana inesperada")
        wid = item.get("id")
        if not isinstance(wid, int) or isinstance(wid, bool):
            raise ValueError("niri: ventana sin id")
        app_id, title = item.get("app_id"), item.get("title")
        out.append(Window(id=wid, app_id=app_id if isinstance(app_id, str) else "",
                          title=title if isinstance(title, str) else "",
                          focused=item.get("is_focused") is True))
    return out


_OUTPUT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def parse_output_name(stdout: str) -> str | None:
    """`niri msg -j focused-output`: the output name (e.g. HDMI-A-1) for `grim -o`, or None when
    there is none or it does not look like a connector name (S17)."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) and _OUTPUT_NAME.fullmatch(name) else None


# --- apps: visible .desktop entries (S14) --------------------------------------------------

MAX_APPS = 500
MAX_QUERY_CHARS = 200
MAX_DESKTOP_BYTES = 256 * 1024
MAX_DESKTOP_FILES = 5000
MAX_APPS_DEPTH = 4
FLATPAK_SYSTEM_EXPORTS = Path("/var/lib/flatpak/exports/share")


def desktop_data_dirs(env: Mapping[str, str], *, home: Path | None = None,
                      flatpak_system: Path | None = None) -> list[Path]:
    """XDG data dirs by precedence: $XDG_DATA_HOME (or ~/.local/share), then $XDG_DATA_DIRS (or
    /usr/local/share:/usr/share), then flatpak exports (user, system) when present and not listed."""
    home = home if home is not None else Path(env.get("HOME") or Path.home())
    flatpak_system = flatpak_system if flatpak_system is not None else FLATPAK_SYSTEM_EXPORTS
    data_home = env.get("XDG_DATA_HOME", "")
    first = Path(data_home) if os.path.isabs(data_home) else home / ".local" / "share"
    dirs_env = env.get("XDG_DATA_DIRS", "") or "/usr/local/share:/usr/share"
    dirs = [first] + [Path(d) for d in dirs_env.split(":") if os.path.isabs(d)]
    for extra in (first / "flatpak" / "exports" / "share", flatpak_system):
        if extra.is_dir():
            dirs.append(extra)
    seen: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return seen


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"s": " ", "n": " ", "t": " ", "r": " "}.get(m[1], m[1]), value)


def _desktop_entry(text: str) -> dict[str, str]:
    """Key/value pairs of the [Desktop Entry] group only (first occurrence of each key wins)."""
    group: str | None = None
    keys: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if group == "Desktop Entry":
                break
            group = line[1:-1]
            continue
        if group == "Desktop Entry" and "=" in line:
            k, v = line.split("=", 1)
            keys.setdefault(k.strip(), _unescape(v.strip()))
    return keys


def _localized(keys: Mapping[str, str], key: str) -> str:
    return keys.get(f"{key}[es]") or keys.get(key) or ""


def _iter_desktop_files(apps_dir: Path) -> Iterable[tuple[str, Path]]:
    """(desktop-id, path) under one applications/ dir, without following directory symlinks."""
    count = 0
    base_depth = len(apps_dir.parts)
    for dirpath, dirnames, filenames in os.walk(apps_dir, followlinks=False):
        if len(Path(dirpath).parts) - base_depth >= MAX_APPS_DEPTH:
            dirnames.clear()
        dirnames.sort()
        for name in sorted(filenames):
            if not name.endswith(".desktop"):
                continue
            count += 1
            if count > MAX_DESKTOP_FILES:
                return
            path = Path(dirpath) / name
            rel = path.relative_to(apps_dir).as_posix()
            yield rel[: -len(".desktop")].replace("/", "-"), path


def _read_entry(path: Path) -> dict[str, str] | None:
    # Follows file symlinks (flatpak exports are symlinks); O_NONBLOCK so a FIFO cannot hang us.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, size = [], 0
        while size <= MAX_DESKTOP_BYTES and (chunk := os.read(fd, MAX_DESKTOP_BYTES + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
        data = b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > MAX_DESKTOP_BYTES:
        return None
    try:
        return _desktop_entry(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def search_key(text: str) -> str:
    return _base(text)


def list_apps(data_dirs: Iterable[Path], *, query: str | None = None,
              cap: int = MAX_APPS) -> tuple[list[dict[str, str]], bool]:
    """Visible applications: [{id, name}] sorted by name, and whether the list was capped.

    Reads files only; never executes anything. The first data dir that has an id wins, even when
    that entry is Hidden/NoDisplay (it masks the lower ones, as in the XDG menu spec).
    """
    seen: set[str] = set()
    found: list[tuple[str, str, str]] = []  # (sort key, id, name)
    needle = search_key(query) if query else ""
    for d in data_dirs:
        apps_dir = d / "applications"
        if not apps_dir.is_dir():
            continue
        for app_id, path in _iter_desktop_files(apps_dir):
            if app_id in seen:
                continue
            seen.add(app_id)
            if validate_desktop_id(app_id):
                continue
            keys = _read_entry(path)
            if not keys or keys.get("Type") != "Application":
                continue
            if keys.get("NoDisplay", "").lower() == "true" or keys.get("Hidden", "").lower() == "true":
                continue
            name = _localized(keys, "Name")[:MAX_TITLE_CHARS]
            if not name:
                continue
            if needle:
                hay = " ".join([name, keys.get("Name", ""), keys.get("GenericName", ""),
                                keys.get("GenericName[es]", ""), keys.get("Keywords", ""),
                                keys.get("Keywords[es]", ""), app_id])
                if needle not in search_key(hay):
                    continue
            found.append((search_key(name), app_id, name))
    found.sort()
    return [{"id": i, "name": n} for _, i, n in found[:cap]], len(found) > cap


# --- what asks the user first (S10a) ------------------------------------------------------

# Browsers: Return there only asks when the window title shows web chat or mail.
_BROWSER_APP_PARTS = (
    "brave", "firefox", "chromium", "chrome", "vivaldi", "opera", "microsoft-edge", "librewolf",
    "waterfox", "floorp", "zen_browser", "zen-browser", "epiphany", "qutebrowser", "falkon", "midori",
)
_BROWSER_APP_IDS = {"zen"}
# Mail and messaging apps (app_id substrings, lowercase): Return / ctrl+Return there sends.
_CHAT_MAIL_APP_PARTS = (
    "thunderbird", "betterbird", "evolution", "geary", "kmail", "mailspring", "claws-mail",
    "tutanota", "protonmail", "proton-mail", "telegram", "kotatogram", "64gram", "materialgram",
    "signal", "discord", "vesktop", "webcord", "armcord", "slack", "whatsapp", "whatsie", "zapzap",
    "element", "riot", "fractal", "nheko", "schildi", "teams", "zoom", "skype", "mattermost",
    "rocket.chat", "ferdium", "franz", "beeper", "viber", "caprine", "messenger", "gajim", "dino",
    "pidgin", "hexchat", "polari", "revolt", "wire",
)
# Web chat and mail, by the browser window title (whole words, after _base: case and accents).
_WEB_CHAT_MAIL_TITLE = re.compile(
    r"\b(whatsapp|telegram|gmail|outlook|hotmail|discord|messenger|slack|teams|proton ?mail|tuta|"
    r"tutanota|yahoo mail|instagram|element|mattermost|rocket\.chat|zoho mail|signal)\b")

REASON_SEND = "enviar en un chat o correo"
REASON_CLEAR = "borrar todo el contenido"
REASON_CLOSE = "cerrar la ventana"

Combo = tuple[frozenset[str], str]
_NO_MODS: frozenset[str] = frozenset()
_SHIFT = frozenset({"shift"})
_COMMAND_MODS = frozenset({"ctrl", "alt", "logo", "altgr"})
_DELETE_KEYS = {"delete", "backspace", "kp_delete"}
_TAB_KEYS = {"tab", "iso_left_tab", "kp_tab"}
_SPACE_KEYS = {"space", "kp_space"}
_HOME_KEYS = {"home", "kp_home"}
_END_KEYS = {"end", "kp_end"}
_PAGE_KEYS = {"page_up", "page_down", "prior", "next", "kp_page_up", "kp_page_down", "kp_prior", "kp_next"}
_TAB_DIGITS = {str(n) for n in range(1, 10)} | {f"kp_{n}" for n in range(1, 10)}
# keysym names that type a character (a bare one replaces a selection, as typed text does)
_CHAR_NAMES = {"space", "colon", "percent", "semicolon", "comma", "period", "minus", "plus", "equal",
               "slash", "underscore", "apostrophe", "quotedbl", "exclam", "question"}
# typed text -> the same (modifiers, key) the keys would give, for the shared rules below
_TEXT_KEYS = {" ": "space", ":": "colon", "%": "percent", "\n": "return", "\t": "tab"}


def _is_colon(c: Combo) -> bool:
    return (c[1] == "colon" and not c[0] & _COMMAND_MODS) or c == (_SHIFT, "semicolon")


def _is_percent(c: Combo) -> bool:
    return (c[1] == "percent" and not c[0] & _COMMAND_MODS) or c == (_SHIFT, "5")


# Vim clears: g g d shift+g, and the ex command :%d (as keys or as typed text).
_VIM_CLEARS: tuple[tuple[Callable[[Combo], bool], ...], ...] = (
    (lambda c: c == (_NO_MODS, "g"), lambda c: c == (_NO_MODS, "g"), lambda c: c == (_NO_MODS, "d"),
     lambda c: c == (_SHIFT, "g")),
    (_is_colon, _is_percent, lambda c: c == (_NO_MODS, "d")),
)
TAIL_COMBOS = 4  # the longest Vim clear: enough to finish one split across calls
TAIL_TTL_S = 60.0
TAIL_WINDOWS = 8  # windows with a remembered tail; the least recently used one is forgotten
_INSERT_KEYS = {"insert", "kp_insert"}
_TYPED_TAB_KEYS = {"tab", "kp_tab"}  # bare Tab types a tab (shift+Tab is ISO_Left_Tab, a move)


def _is_browser(app_id: str) -> bool:
    return app_id in _BROWSER_APP_IDS or any(p in app_id for p in _BROWSER_APP_PARTS)


def is_chat_or_mail(app_id: str, title: str) -> bool:
    """A mail or messaging app, or a browser window whose title shows web chat or mail."""
    a = app_id.casefold()
    if not a:
        return False
    if any(p in a for p in _CHAT_MAIL_APP_PARTS):
        return True
    return _is_browser(a) and _WEB_CHAT_MAIL_TITLE.search(_base(title)) is not None


def key_combos(args: list[str]) -> list[Combo]:
    """parse_keys output -> (modifiers, lowercase key) per press, in order. A bare argument is the
    capital parse_keys sends for shift+letter."""
    combos: list[Combo] = []
    held: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-M":
            held.append(args[i + 1])
            i += 2
        elif a == "-m":
            if args[i + 1] in held:
                held.remove(args[i + 1])
            i += 2
        elif a == "-k":
            combos.append((frozenset(held), args[i + 1].lower()))
            i += 2
        elif a == "-s":
            i += 2
        else:
            combos += [(_SHIFT, ch.lower()) for ch in a]
            i += 1
    return combos


def text_combos(text: str) -> list[Combo]:
    """Typed text as the presses it amounts to: a capital is shift+letter, ":" colon, "\n" Return."""
    return [(_NO_MODS, _TEXT_KEYS[ch]) if ch in _TEXT_KEYS
            else (_SHIFT, ch.lower()) if ch.isupper() else (_NO_MODS, ch) for ch in text]


@dataclass(frozen=True)
class KeyTail:
    """What the previous calls on one window left behind (L17 (a)): the last presses, whether
    everything is still selected, and a ctrl+Home/ctrl+End that a ctrl+shift+End/Home completes."""
    recent: tuple[Combo, ...] = ()
    select_all: bool = False
    corner: str = ""


def _scan(tail: KeyTail, combos: list[Combo]) -> tuple[bool, KeyTail]:
    """Whether `combos`, after `tail`, clear all content (a match that ends in `combos`; one that
    ended in the tail was already asked), and the tail they leave."""
    seq = list(tail.recent) + combos
    select_all, corner, clears = tail.select_all, tail.corner, False
    for i, (mods, key) in enumerate(combos):
        j = len(tail.recent) + i
        replaces = not mods & _COMMAND_MODS and (len(key) == 1 or key in _CHAR_NAMES or key in _RETURN_KEYS)
        replaces = replaces or (not mods and key in _TYPED_TAB_KEYS)  # T16: Return/Tab over all
        pastes = ("ctrl" in mods and key == "v") or (mods == _SHIFT and key in _INSERT_KEYS)
        if select_all and (key in _DELETE_KEYS or ("ctrl" in mods and key == "x") or replaces or pastes):
            clears, select_all = True, False  # the selection is gone after it
        if "ctrl" in mods and key == "a":
            select_all = True
        if "ctrl" in mods and key in _HOME_KEYS | _END_KEYS:
            edge = "home" if key in _HOME_KEYS else "end"
            if "shift" in mods and corner and corner != edge:
                select_all = True
            elif "shift" not in mods:
                corner = edge
        for pattern in _VIM_CLEARS:
            n = len(pattern)
            if j + 1 >= n and all(p(c) for p, c in zip(pattern, seq[j + 1 - n:j + 1])):
                clears = True
    return clears, KeyTail(tuple(seq[-TAIL_COMBOS:]), select_all, corner)


def _closes(combos: list[Combo]) -> bool:
    return any(("alt" in mods and key == "f4") or ("ctrl" in mods and key in ("w", "q", "f4"))
               or ("logo" in mods and key in ("w", "q")) or ({"logo", "shift"} <= mods and key == "e")
               or ({"ctrl", "alt"} <= mods and key in ("delete", "kp_delete"))
               for mods, key in combos)


def _followed(combos: list[Combo], first: Callable[[Combo], bool], then: Callable[[Combo], bool]) -> bool:
    seen = False
    for c in combos:
        if seen and then(c):
            return True
        seen = seen or first(c)
    return False


def _switches_tab(c: Combo) -> bool:
    mods, key = c
    return (("ctrl" in mods and (key in _TAB_KEYS or key in _PAGE_KEYS or key in _TAB_DIGITS))
            or ("alt" in mods and key in _TAB_DIGITS))


def _sends(app_id: str, title: str, keys: list[str] | None, text: str | None, combos: list[Combo]) -> bool:
    returns = (keys is not None and presses_return(keys)) or (text is not None and "\n" in text)
    if is_chat_or_mail(app_id, title):
        # Return, or Tab to the Send button and space/Return on it
        return returns or _followed(combos, lambda c: c[1] in _TAB_KEYS,
                                    lambda c: c[1] in _SPACE_KEYS or c[1] in _RETURN_KEYS)
    # a tab switch inside the sequence may land on web chat whatever the title is now
    return _is_browser(app_id.casefold()) and _followed(combos, _switches_tab, lambda c: c[1] in _RETURN_KEYS)


def _combos(keys: list[str] | None, text: str | None) -> list[Combo]:
    return (key_combos(keys) if keys is not None else []) + (text_combos(text) if text is not None else [])


def confirm_reasons(app_id: str, title: str, *, keys: list[str] | None = None,
                    text: str | None = None, tail: KeyTail = KeyTail()) -> list[str]:
    """Why this action must ask the user first (S10a, L17), in Spanish, or [] when it needs no prompt.

    By the action only, never by words in the text or the intent:
    - send: Return (any Return keysym, or a newline in the text), or Tab then space/Return, in a
      chat or mail window; in any browser, a tab switch (ctrl+Tab, ctrl+PageDown, ctrl/alt+1..9)
      later followed by Return;
    - clear all: a select-all (ctrl+a, or ctrl+Home then ctrl+shift+End) then Delete/KP_Delete/
      BackSpace/ctrl+x, a paste (ctrl+v, shift+Insert), Return/KP_Enter, Tab, or a typed character
      (a typed newline or tab too); Vim's g g d shift+g or :%d, as keys or typed text.
      Matched against `tail` (the previous calls on the same window) plus this action;
    - close: alt+F4, ctrl+w, ctrl+q, ctrl+F4, super+w, super+q, super+shift+e, ctrl+alt+Delete.
    `keys` is parse_keys output."""
    combos = _combos(keys, text)
    reasons: list[str] = []
    if _sends(app_id, title, keys, text, combos):
        reasons.append(REASON_SEND)
    if _scan(tail, combos)[0]:
        reasons.append(REASON_CLEAR)
    if _closes(combos):
        reasons.append(REASON_CLOSE)
    return reasons


def next_tail(tail: KeyTail, *, keys: list[str] | None = None, text: str | None = None,
              failed: bool = False) -> KeyTail:
    """The tail after this action ran on the same window. `failed`: wtype ran but failed, so any
    part of it may have been typed: its presses count, and a select-all or ctrl+Home/End pending
    before it is kept (T16)."""
    after = _scan(tail, _combos(keys, text))[1]
    if not failed:
        return after
    return KeyTail(after.recent, after.select_all or tail.select_all, after.corner or tail.corner)


class KeyTails:
    """The tail of the last action typed into each window (L17 (a), T16): memory only, at most
    `size` windows (the least recently recorded one is forgotten), each entry forgotten `ttl`
    seconds after it was recorded."""

    def __init__(self, ttl: float = TAIL_TTL_S, clock: Callable[[], float] = time.monotonic,
                 size: int = TAIL_WINDOWS) -> None:
        self.ttl, self.clock, self.size = ttl, clock, size
        self._tails: OrderedDict[int, tuple[KeyTail, float]] = OrderedDict()

    def get(self, wid: int) -> KeyTail:
        entry = self._tails.get(wid)
        if entry is None:
            return KeyTail()
        tail, at = entry
        if self.clock() - at > self.ttl:
            del self._tails[wid]
            return KeyTail()
        return tail

    def record(self, wid: int, tail: KeyTail) -> None:
        self._tails[wid] = (tail, self.clock())
        self._tails.move_to_end(wid)
        while len(self._tails) > self.size:
            self._tails.popitem(last=False)


# --- open ----------------------------------------------------------------------------------

_DESKTOP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")


def validate_desktop_id(app: object) -> str | None:
    if not isinstance(app, str) or not _DESKTOP_ID.fullmatch(app):
        return "app debe ser un desktop-id (letras, números, '.', '_' o '-'; sin rutas ni espacios)"
    return None


def validate_url(url: object) -> str | None:
    bad = "url debe ser http:// o https:// con dominio, sin espacios ni caracteres de control"
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        return bad
    if any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf") for c in url):
        return bad
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return bad
    if parts.scheme not in ("http", "https") or not url.lower().startswith(parts.scheme + "://"):
        return bad
    if not parts.netloc or not host:
        return bad
    return None


# --- media and volume (T15) ---------------------------------------------------------------

# The C4 catalog's media/volume set, each with a fixed argv: no caller text ever reaches it.
# Volume moves in 5% steps on the default sink; raising it is capped at 100% (-l 1.0).
_SINK = "@DEFAULT_AUDIO_SINK@"
_MEDIA_ARGV: dict[str, tuple[str, ...]] = {
    "play_pause": ("playerctl", "play-pause"),
    "next": ("playerctl", "next"),
    "previous": ("playerctl", "previous"),
    "volume_up": ("wpctl", "set-volume", "-l", "1.0", _SINK, "5%+"),
    "volume_down": ("wpctl", "set-volume", _SINK, "5%-"),
    # Explicit states, not a toggle: "silencio" must never unmute an already muted sink.
    "mute": ("wpctl", "set-mute", _SINK, "1"),
    "unmute": ("wpctl", "set-mute", _SINK, "0"),
}
MEDIA_ACTIONS = tuple(_MEDIA_ARGV)


def media_argv(action: object) -> list[str]:
    """A media action name -> its fixed argv. Raises ValueError for anything outside the set."""
    if not isinstance(action, str) or action not in _MEDIA_ARGV:
        raise ValueError("action debe ser una de: " + ", ".join(MEDIA_ACTIONS))
    return list(_MEDIA_ARGV[action])


# --- rate limit ----------------------------------------------------------------------------


class RateLimiter:
    """Sliding window: a call returns True while fewer than `max` actions ran in the last `window` s."""

    def __init__(self, max: int, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max, self.window, self.clock = max, window, clock
        self._times: deque[float] = deque()

    def __call__(self) -> bool:
        now = self.clock()
        while self._times and self._times[0] <= now - self.window:
            self._times.popleft()
        if len(self._times) >= self.max:
            return False
        self._times.append(now)
        return True
