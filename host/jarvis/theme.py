"""Colour themes shared by both views (the approved unified theme design, L14-L16).

Six token sets, copied by value from the approved design so the host never reads the
design folder at run time. Every view draws with short class codes (``t1``, ``lv``,
``bc``…) and ``style`` turns them into colours of the active theme, so no view holds a
colour of its own. Colour carries state only: live, ok, over target and failed; the
accent and its gradient are the brand (Jarvis mark, prompt, hero lettering, bars).

The app has a dark mode and a light mode. ``DEFAULT_DARK`` names the dark theme the
window starts in and ``LIGHT`` the light one; ``--theme`` starts in any of the six.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from rich.style import Style
from rich.text import Text

#: The dark theme the window starts in and returns to with f3 (L17: Gentleman Cute).
DEFAULT_DARK: Final = "gentleman-cute"
#: The light theme f3 switches to.
LIGHT: Final = "paper"


@dataclass(frozen=True, slots=True)
class Theme:
    """Semantic colour tokens of one theme."""

    name: str
    title: str
    dark: bool
    background: str  # window and card background
    surface: str  # input row
    message: str  # user message bubble
    border: str  # card edges, rules, table rails
    dim: str  # non-text decoration only (bar tracks, ╎ more above)
    text: str  # primary text
    text2: str  # secondary text and panel titles
    muted: str  # meta, units, times, key labels
    accent: str  # brand only: ◆ Jarvis mark, ▸ prompt and cursor
    live: str  # ◐ live / working / listening / running
    ok: str  # ✓ ok, ● connected
    over: str  # ▲ over target
    failed: str  # ✗ failed
    over_tint: str  # over-target row
    failed_tint: str  # failed row
    gradient: tuple[str, ...]  # 12 steps: hero lettering, card pulse, every bar and meter

    @property
    def gradient_start(self) -> str:
        return self.gradient[0]

    @property
    def gradient_end(self) -> str:
        return self.gradient[-1]


THEMES: Final[dict[str, Theme]] = {
    "teal": Theme(
        name="teal", title="Teal", dark=True,
        background="#0e1b1d", surface="#182b2e", message="#182b2e", border="#2d474a",
        dim="#3f5c5f", text="#e6f0ed", text2="#aec4bf", muted="#8ba7a1", accent="#76d6bf",
        live="#bba7f5", ok="#b3d98a", over="#eac770", failed="#f59a8c",
        over_tint="#222a24", failed_tint="#252828",
        gradient=("#76d6bf", "#75d5c3", "#73d4c7", "#72d3cc", "#71d2d0", "#70d1d4",
                  "#71cfd8", "#75ccdc", "#79cae0", "#7ec8e4", "#82c5e8", "#86c3ec"),
    ),
    "plum": Theme(
        name="plum", title="Plum", dark=True,
        background="#19141d", surface="#251d2b", message="#2a2131", border="#4a3d54",
        dim="#5a4b66", text="#f3ebf5", text2="#cdc0d3", muted="#a898b0", accent="#f4a873",
        live="#7fd3e6", ok="#a8d98c", over="#f0d576", failed="#ff6b6b",
        over_tint="#2c2525", failed_tint="#301d25",
        gradient=("#f4a873", "#efa87f", "#e9a88a", "#e4a896", "#dfa8a1", "#d9a8ad",
                  "#d4a9b8", "#cea9c4", "#c9a9cf", "#c4a9db", "#bea9e6", "#b9a9f2"),
    ),
    "graphite": Theme(
        name="graphite", title="Graphite", dark=True,
        background="#121418", surface="#1b1f25", message="#20252c", border="#343b45",
        dim="#46505c", text="#e7eaee", text2="#b4bbc5", muted="#8e97a4", accent="#7aa7f8",
        live="#c6a3ff", ok="#93d4a0", over="#e8c374", failed="#f48a80",
        over_tint="#252420", failed_tint="#292022",
        gradient=("#7aa7f8", "#79acf7", "#77b1f5", "#76b6f4", "#75bbf2", "#74c0f1",
                  "#74c5ef", "#76caed", "#78ceeb", "#7bd3e8", "#7dd7e6", "#7fdce4"),
    ),
    "paper": Theme(
        name="paper", title="Paper", dark=False,
        background="#f5f1e8", surface="#ebe5d8", message="#e8e1d1", border="#cbc1ad",
        dim="#b5aa94", text="#221f1a", text2="#474136", muted="#665e50", accent="#3446c2",
        live="#94309f", ok="#246a2d", over="#8c5300", failed="#b02c22",
        over_tint="#efe6d2", failed_tint="#f1e3da",
        gradient=("#3446c2", "#314abb", "#2f4db5", "#2c51ae", "#2a55a8", "#2758a1",
                  "#235b99", "#1e5c90", "#185e87", "#125f7e", "#0d6175", "#07626c"),
    ),
    "gentleman-cute": Theme(
        name="gentleman-cute", title="Gentleman Cute", dark=True,
        background="#060407", surface="#100a0f", message="#2a1523", border="#563040",
        dim="#76616b", text="#f6eff3", text2="#d2cbd0", muted="#a78e9b", accent="#f095c8",
        live="#a9c7ee", ok="#b4e7c7", over="#f2b86d", failed="#ff7272",
        over_tint="#1b1410", failed_tint="#261019",
        gradient=("#ffb1dd", "#fcacd9", "#faa7d5", "#f7a2d2", "#f49dce", "#f198ca",
                  "#ec91c5", "#e589be", "#de81b7", "#d77ab0", "#d072a9", "#c96aa2"),
    ),
    "gentleman-sexy": Theme(
        name="gentleman-sexy", title="Gentleman Sexy", dark=True,
        background="#060407", surface="#100a0f", message="#2e0f1f", border="#563040",
        dim="#76616b", text="#f6eff3", text2="#d2cbd0", muted="#a78e9b", accent="#f43888",
        live="#a9c7ee", ok="#b4e7c7", over="#f2b86d", failed="#ff7272",
        over_tint="#1b1410", failed_tint="#261019",
        gradient=("#f43888", "#f04193", "#eb4a9e", "#e753a8", "#e35cb3", "#de65be",
                  "#da6ec9", "#d577d4", "#d180df", "#cd89e9", "#c892f4", "#c49bff"),
    ),
}

# --- Class codes ----------------------------------------------------------------

#: Foreground class codes and the token each one reads.
FOREGROUND: Final = {
    "t1": "text", "t2": "text2", "mu": "muted", "ac": "accent", "lv": "live",
    "ok": "ok", "wa": "over", "er": "failed", "bd": "border", "dm": "dim",
}
#: Background class codes and the token each one reads.
BACKGROUND: Final = {
    "bc": "background", "bs": "surface", "bb": "message", "bw": "over_tint", "be": "failed_tint",
}
GRADIENT_STEPS: Final = 12


def gradient_class(index: int, count: int) -> str:
    """The gradient class of cell ``index`` of a run of ``count`` cells."""

    step = round(index / max(1, count - 1) * (GRADIENT_STEPS - 1))
    return f"h{min(GRADIENT_STEPS - 1, max(0, step))}"


def style(theme: Theme, classes: str) -> Style:
    """The Rich style of a space-separated list of class codes in ``theme``."""

    fg: str | None = None
    bg: str | None = None
    for code in classes.split():
        if code in FOREGROUND:
            fg = getattr(theme, FOREGROUND[code])
        elif code in BACKGROUND:
            bg = getattr(theme, BACKGROUND[code])
        elif code[:1] == "h" and code[1:].isdigit():
            fg = theme.gradient[min(GRADIENT_STEPS - 1, int(code[1:]))]
    parts = classes.split()
    return Style(color=fg, bgcolor=bg, bold="b" in parts, italic="i" in parts)


def to_text(rows: Sequence[Sequence[tuple[str, str]]], theme: Theme) -> Text:
    """Rows of ``(text, classes)`` segments as one Rich ``Text`` in ``theme``."""

    cache: dict[str, Style] = {}
    text = Text(no_wrap=True, overflow="crop", end="")
    for index, row in enumerate(rows):
        if index:
            text.append("\n")
        for chunk, classes in row:
            found = cache.get(classes)
            if found is None:
                found = cache[classes] = style(theme, classes)
            text.append(chunk, found)
    return text


# --- Contrast -------------------------------------------------------------------


def _luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two ``#rrggbb`` colours."""

    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)
