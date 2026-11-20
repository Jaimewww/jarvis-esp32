# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""PC control in the window (S13): the ``/control`` indicator and the confirmation card.

Both sit above the input, so the user view and the developer view show them alike. The
card answers ``jarvis-input``'s ``confirm`` events and the daemon's ``confirm`` asks alike.
"""

from __future__ import annotations

import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from rich.text import Text
from textual.geometry import Size
from textual.widget import Widget

from .client import Ask
from .control import ControlState, remaining_label
from .devview import Seg, place
from .theme import DEFAULT_DARK, THEMES, Theme, to_text
from .userview import region

#: Lines of the asker's message the card shows; the rest is cut with an ellipsis.
MESSAGE_LINES = 4


@dataclass(slots=True)
class PendingConfirm:
    """A confirmation waiting for y or n (monotonic times)."""

    ask: Ask
    deadline: float
    #: Keys before this are ignored: a ``y`` typed by habit as the card appears is no answer.
    armed_at: float
    #: Who asked: ``"daemon"`` (answered with ``ui_response``) or ``"helper"`` (``answer``).
    source: str = "daemon"
    #: The helper's ``id`` as it came, echoed in the ``answer``.
    reply_id: object = None


def control_row(remaining_s: float, w: int) -> list[Seg]:
    sep = (" · ", "mu")
    segs = [("◉", "wa b"), (" control on", "t1 b"), sep, (f"{remaining_label(remaining_s)} left", "t1"),
            sep, ("/control off", "t2 b")]
    return place([(2, segs)], w, "bc")


def unavailable_row(w: int) -> list[Seg]:
    sep = (" · ", "mu")
    segs = [("◌", "mu b"), (" control unavailable", "t2 b"), sep, ("jarvis-input is not running", "mu")]
    return place([(2, segs)], w, "bc")


class ControlBar(Widget):
    """``◉ control on · 29:12 left · /control off`` while the helper reports control on;
    ``◌ control unavailable`` while the helper cannot be reached."""

    DEFAULT_CSS = """
    ControlBar {
        height: 1;
        background: $jarvis-background;
    }
    """

    def __init__(self, clock: Callable[[], datetime], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.clock = clock
        self.state: ControlState | None = None
        self.unavailable = False
        self.palette: Theme = THEMES[DEFAULT_DARK]

    @property
    def shown(self) -> bool:
        return self.unavailable or self.on

    @property
    def on(self) -> bool:
        return self.state is not None and self.state.remaining_s(self.clock()) > 0

    def render(self) -> Text:
        if self.unavailable:
            return to_text([unavailable_row(self.size.width)], self.palette)
        if not self.on or self.state is None:
            return Text("")
        return to_text([control_row(self.state.remaining_s(self.clock()), self.size.width)], self.palette)


def _framed(inner: list[Seg], w: int) -> list[Seg]:
    return [("┃", "wa b")] + region([(" ", "")] + inner, w - 2) + [("┃", "wa b")]


def confirm_rows(pending: PendingConfirm, queued: int, now: float, w: int) -> list[list[Seg]]:
    """The card: a heavy warning frame, the question, and only explicit keys."""

    if w < 12:
        return [region([("y allow · n deny", "wa b")], w)]
    room = w - 4
    ask = pending.ask
    title: list[Seg] = [("━ ", "wa b"), ("⚠ Confirm", "wa b"), ("  Jarvis wants to act on your PC", "mu")]
    if queued:
        title.append((f"  +{queued} waiting", "mu"))
    # The frame line runs to the right edge behind the title; a long title is cut.
    top = [("┏", "wa b")] + region(title + [(" ", ""), ("━" * w, "wa b")], w - 2) + [("┓", "wa b")]
    rows: list[list[Seg]] = [top]
    rows.append(_framed([(ask.title or "Allow this action?", "t1 b")], w))
    if ask.message:
        lines = textwrap.wrap(ask.message, room) or [""]
        if len(lines) > MESSAGE_LINES:
            lines = lines[:MESSAGE_LINES]
            lines[-1] = lines[-1][: max(0, room - 1)] + "…"
        rows += [_framed([(line, "t2")], w) for line in lines]
    left = max(0, int(pending.deadline - now + 0.999))
    sep = ("  ·  ", "mu")
    keys: list[Seg] = [("y", "ok b"), (" allow", "t1"), sep, ("n", "er b"), (" deny", "t1"), sep,
                       ("esc", "t2 b"), (" deny", "mu"), sep, (f"denied in {left}s", "mu")]
    rows.append(_framed(keys, w))
    rows.append([("┗", "wa b"), ("━" * (w - 2), "wa b"), ("┛", "wa b")])
    return rows


class ConfirmCard(Widget):
    """The first waiting confirmation; hidden while none waits."""

    DEFAULT_CSS = """
    ConfirmCard {
        height: auto;
        background: $jarvis-background;
    }
    """

    def __init__(self, monotonic: Callable[[], float] = time.monotonic, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.monotonic = monotonic
        self.pending: list[PendingConfirm] = []
        self.palette: Theme = THEMES[DEFAULT_DARK]

    def _rows(self, w: int) -> list[list[Seg]]:
        if not self.pending:
            return []
        return confirm_rows(self.pending[0], len(self.pending) - 1, self.monotonic(), w)

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._rows(width))

    def render(self) -> Text:
        return to_text(self._rows(self.size.width), self.palette)
