# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""Developer view: the approved mockup ``design/tui-developer/06-pipeline-refined.html``,
repainted with the unified conventions of the theme artboards (``design/tui-theme``).

Every row is laid out on one shared column grid, as in the mockup generator
(``design/tui-developer/.preview/opt6.py``): glyph, time or name, turn id, heard text,
then lane, bar against the lane target, total, over-by and four stage columns anchored
to the right edge. The device status and the live-turn pipeline share one rounded card
(the device in its top border, a gradient pulse under the live stage), the agent and
its PC actions another; open sections have a sentence-case title followed by a rule.
Colours come only from the active theme (``jarvis.theme``) and mark state only: live,
within, over target and failed; the gradient fills the bars. Everything comes from
``HostModel``; with no data each region shows an empty state instead of demo figures.

At 140 columns the grid is the mockup's. Narrower windows drop the stage columns and
then the bars; fewer rows shrink the turn table first.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from rich.text import Text
from textual.widget import Widget

from .client import ConnectionStatus
from .model import STAGES, HostModel, StageState, Turn
from .theme import DEFAULT_DARK, THEMES, Theme, gradient_class, to_text

Seg = tuple[str, str]
Item = tuple[int, list[Seg]]

LIVE: Final[Seg] = ("◐", "lv")
OVER: Final[Seg] = ("▲", "wa b")
FAIL: Final[Seg] = ("✗", "er b")
OK: Final[Seg] = ("✓", "ok")
WAIT: Final[Seg] = ("○", "mu")
DOT: Final[Seg] = ("·", "mu")

PAD: Final = 2
X_G, X_T, X_ID, X_H = 2, 4, 11, 21
BAR_N, BAR_X = 20, 5
SEC_W, STAGE_W = 6, 7
LOWER_FULL: Final = 15
LIVE_WORDS: Final = {"stt": "listening", "route": "routing", "agent": "thinking", "tts": "speaking"}
LINKS: Final = {
    ConnectionStatus.CONNECTED: (("●", "ok"), "connected"),
    ConnectionStatus.CONNECTING: (LIVE, "connecting"),
    ConnectionStatus.DISCONNECTED: (WAIT, "disconnected"),
    ConnectionStatus.UNAUTHORIZED: (FAIL, "unauthorized"),
    ConnectionStatus.PROTOCOL_MISMATCH: (FAIL, "bad protocol"),
}
QUIET_STATES: Final = frozenset({"starting", "restarting", "unknown", "offline"})
LEGEND: Final = (
    (("●", "ok"), "connected"),
    (LIVE, "live"),
    (WAIT, "waiting"),
    (OK, "within"),
    (OVER, "over target"),
    (FAIL, "failed"),
    (("┊", "t2"), "lane target"),
)
#: Legend entries dropped first when the footer is short of room.
LEGEND_DROP_ORDER: Final = ("connected", "waiting", "lane target", "within", "live", "over target", "failed")


@dataclass(frozen=True, slots=True)
class Grid:
    """Absolute columns for one window width."""

    w: int
    right: int  # last content cell
    x_l: int
    x_b: int | None  # bar; None when too narrow
    x_tot: int
    x_ob: int
    x_st: int | None  # first stage column; None when too narrow
    split: int  # the rail starts here

    @property
    def heard_w(self) -> int:
        return max(0, self.x_l - X_H - 2)


def grid(w: int) -> Grid:
    right = w - PAD - 1
    if w >= 120:
        x_st: int | None = right + 1 - 3 - 4 * STAGE_W
        x_ob = x_st - (SEC_W + 2)
    else:
        x_st = None
        x_ob = right + 1 - SEC_W
    x_tot = x_ob - (SEC_W + 2)
    x_b: int | None = x_tot - (BAR_N + 1 + BAR_X + 2) if w >= 96 else None
    x_l = (x_b if x_b is not None else x_tot) - 6
    split = x_tot - PAD if x_st is not None else max(w // 2, w - 51)
    return Grid(w, right, x_l, x_b, x_tot, x_ob, x_st, split)


# --- Row building ---------------------------------------------------------------


def S(text: str, cls: str = "") -> Seg:
    return (text, cls)


def width(segs: Sequence[Seg]) -> int:
    return sum(len(text) for text, _ in segs)


def clip(text: str, n: int) -> str:
    if n <= 0:
        return ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _cut(segs: list[Seg], n: int) -> list[Seg]:
    out: list[Seg] = []
    left = n
    for text, cls in segs:
        if left <= 0:
            break
        out.append((text[:left], cls))
        left -= len(text[:left])
    return out


def place(
    items: Sequence[Item], w: int, bg: str, x0: int = 0, right: list[Seg] | None = None, edge: int | None = None
) -> list[Seg]:
    """One region of ``w`` cells starting at absolute ``x0``; ``right`` ends at ``edge``."""

    out: list[Seg] = []
    cur = x0
    end = x0 + w
    for x, segs in sorted(items, key=lambda item: item[0]):
        if x < cur:  # a long value pushes the next one, never overlaps it
            x = cur + 1 if cur > x0 else cur
        if x >= end:
            break
        if x > cur:
            out.append(S(" " * (x - cur)))
        segs = _cut(segs, end - x)
        out += segs
        cur = x + width(segs)
    if right:
        stop = (edge if edge is not None else end - PAD - 1) + 1
        rx = stop - width(right)
        if rx > cur and stop <= end:
            out += [S(" " * (rx - cur))] + right
            cur = stop
    if cur < end:
        out.append(S(" " * (end - cur)))
    return [(text, f"{cls} {bg}".strip()) for text, cls in out]


def blank(w: int, bg: str) -> list[Seg]:
    return [(" " * w, bg)]


def header(title: str, meta: str = "", extra: Sequence[list[Seg]] = ()) -> list[Seg]:
    segs = [S(title, "t2 b")]
    if meta:
        segs.append(S("  " + meta, "mu"))
    for part in extra:
        segs += [S("   ")] + part
    return segs


def cells(row: Sequence[Seg]) -> list[Seg]:
    """One ``(char, classes)`` pair per cell."""

    return [(ch, cls) for text, cls in row for ch in text]


def merge(row: Sequence[Seg]) -> list[Seg]:
    out: list[Seg] = []
    for text, cls in row:
        if out and out[-1][1] == cls:
            out[-1] = (out[-1][0] + text, cls)
        else:
            out.append((text, cls))
    return out


def sides(row: Sequence[Seg], x0: int, x1: int) -> list[Seg]:
    """``row`` with the card's side lines at absolute ``x0`` and ``x1``."""

    out = cells(row)
    for x in (x0, x1):
        if 0 <= x < len(out):
            bg = " ".join(c for c in out[x][1].split() if c in ("bc", "bs", "bw", "be"))
            out[x] = ("│", f"bd {bg}".strip())
    return merge(out)


def rule_to(segs: list[Seg], x_start: int, x_end: int) -> list[Seg]:
    """``segs`` (starting at ``x_start``) followed by a space and a rule up to ``x_end``."""

    n = x_end - (x_start + width(segs) + 1) + 1
    return segs + [S(" "), S("─" * n, "bd")] if n > 0 else segs


def card_top(
    x0: int, x1: int, title: list[Seg], inline: Sequence[Item] = (), right: list[Seg] | None = None
) -> list[Seg]:
    """``╭─ Title  inline … ── right ─╮`` from ``x0`` to ``x1``, on the canvas."""

    right_part = ([S(" ")] + right + [S(" "), S("─╮", "bd")]) if right else [S("╮", "bd")]
    segs: list[Seg] = [S("╭─", "bd"), S(" ")] + title
    cur = x0 + width(segs)
    stop = x1 + 1 - width(right_part)
    if right and cur + 2 > stop:  # too narrow for the right-hand meta
        right_part, stop = [S("╮", "bd")], x1
    for x, part in inline:
        x = max(x, cur + 2)
        if x + width(part) + 2 > stop:
            break
        segs += [S(" " * (x - cur))] + part
        cur = x + width(part)
    if stop - cur >= 2:
        segs += [S(" "), S("─" * (stop - cur - 1), "bd")]
    else:
        segs = _cut(segs, stop - x0)
        segs.append(S(" " * (stop - x0 - width(segs))))
    return [(text, f"{cls} bc".strip()) for text, cls in segs + right_part]


def card_bottom(x0: int, x1: int, pulse: tuple[int, int] | None = None) -> list[Seg]:
    """``╰───╯`` from ``x0`` to ``x1``; ``pulse`` = (absolute x, length) in the gradient."""

    out = [S("╰", "bd")] + [S("─", "bd")] * (x1 - x0 - 1) + [S("╯", "bd")]
    if pulse is not None:
        at, n = pulse
        for j in range(n):
            if x0 < at + j < x1:
                out[at + j - x0] = S("━", gradient_class(j, n))
    return merge([(text, f"{cls} bc") for text, cls in out])


# --- Formatting -----------------------------------------------------------------


def fmt_s(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds:.1f} s"


def clock(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "--:--"


def link(name: str, status: ConnectionStatus) -> list[Seg]:
    glyph, word = LINKS.get(status, (WAIT, status.value))
    return [S(glyph[0] + " ", glyph[1]), S(name + " ", "mu"), S(word, "t2")]


def state_glyph(state: str) -> tuple[Seg, str]:
    """The glyph and text class of a device or agent state."""

    if state == "idle":
        return ("●", "ok"), "t1 b"
    if state == "error":
        return FAIL, "er b"
    if state in QUIET_STATES:
        return WAIT, "t2 b"
    return LIVE, "lv b"


# --- Regions --------------------------------------------------------------------


def device_row(model: HostModel, g: Grid) -> list[Seg]:
    """The top border of the status card: device, its state and links, both connections."""

    items: list[Item] = []
    devices = list(model.devices.values())
    if not devices:
        title = [S("no device yet", "mu i")]
    else:
        device = devices[0]
        title = [S(device.device_id, "t2 b")]
        glyph, cls = state_glyph(device.state)
        items.append((X_ID + 1, [S(glyph[0] + " ", glyph[1]), S(device.state.upper(), cls)]))
        if device.rssi is not None:
            items.append((X_H + 3, [S(f"{device.rssi} dBm", "t2")]))
        if device.ip:
            items.append((X_H + 13, [S(device.ip, "t2")]))
        if device.online:
            items.append((X_H + 29, [S("● ", "ok"), S("online", "t2")]))
        else:
            items.append((X_H + 29, [S("✗ ", "er b"), S("offline", "t2")]))
        if len(devices) > 1:
            items.append((X_H + 39, [S(f"+{len(devices) - 1} more", "mu")]))
    right = link("daemon", model.daemon_status) + [S("   ")] + link("backend", model.backend_status)
    return card_top(0, g.w - 1, title, items, right)


def live_band(model: HostModel, g: Grid) -> list[list[Seg]]:
    turn = model.live_turn()
    medians = model.stage_medians()
    space = g.right + 1 - X_H
    arw = 7 if (space - 21) // 4 >= 18 else 3
    boxw = max(8, (space - 3 * arw) // 4)
    xs = [X_H + i * (boxw + arw) for i in range(4)]
    arrow = "  ──→  " if arw == 7 else " → "
    # One rule per kind of word, sized for the longest stage name: a kind of word shows
    # in every box or in none, never cut and never in some boxes only.
    word_room = boxw - 2 - (3 + max(len(name) for name in STAGES)) - 2
    show_live = word_room >= max(len(word) for word in LIVE_WORDS.values())
    show_ms = word_room >= len("0000 ms")
    show_waiting = word_room >= len("waiting")

    def box(i: int, kind: str) -> list[Seg]:
        name = STAGES[i]
        stage = turn.stage(name) if turn else None
        state = stage.state if stage else None
        live = state is StageState.LIVE
        border = "lv" if live else "bd"
        if kind == "top":
            return [S("╭" + "─" * (boxw - 2) + "╮", border)]
        if kind == "bot":
            return [S("╰" + "─" * (boxw - 2) + "╯", border)]
        if live:
            glyph, label, word = LIVE, "lv b", [S(LIVE_WORDS[name], "lv")] if show_live else []
        elif state is StageState.DONE and stage is not None:
            ms = "?" if stage.ms is None else f"{stage.ms:.0f}"
            glyph, label, word = OK, "t2", [S(ms, "t1"), S(" ms", "mu")] if show_ms else []
        elif turn is None:
            glyph, label, word = WAIT, "t2", [S("—", "mu")]
        else:
            glyph, label, word = WAIT, "t2", [S("waiting", "mu")] if show_waiting else []
        left = [S(" "), S(*glyph), S(" "), S(name, label)]
        gap = boxw - 2 - width(left) - width(word) - 1
        if gap < 1:
            word, gap = [], boxw - 2 - width(left) - 1
        return [S("│", border)] + left + [S(" " * max(0, gap))] + word + [S(" "), S("│", border)]

    def row(label: list[Item], kind: str) -> list[Seg]:
        items = list(label)
        for i in range(4):
            if kind == "med":
                value = medians[STAGES[i]]
                seg = [S("—", "mu")] if value is None else [S(f"{value:.0f}", "t1"), S(" ms", "mu")]
                items.append((xs[i] + boxw - 2 - width(seg), seg))
            else:
                items.append((xs[i], box(i, kind)))
                if i < 3 and kind == "mid":
                    items.append((xs[i] + boxw, [S(arrow, "mu")]))
        return sides(place(items, g.w, "bc"), 0, g.w - 1)

    if turn is None:
        mid: list[Item] = [(X_G, [S(*WAIT)]), (X_T, [S("idle", "mu")])]
        bot: list[Item] = [(X_G, [S("no live turn", "mu i")])]
    else:
        mid = [(X_G, [S(*LIVE)]), (X_T, [S(clock(turn.started_at), "mu")]),
               (X_ID, [S(turn.turn_id[:8] or "?", "t1 b")])]
        bot = [(X_G, [S("lane — until done", "mu i")])]
    live = next(
        (i for i, name in enumerate(STAGES) if turn and turn.stage(name).state is StageState.LIVE), None
    )
    pulse = None if live is None else (xs[live], boxw)
    return [
        sides(blank(g.w, "bc"), 0, g.w - 1),
        row([(X_G, header("Live turn"))], "top"),
        row(mid, "mid"),
        row(bot, "bot"),
        row([(X_G, [S("stage median", "mu")])], "med"),
        card_bottom(0, g.w - 1, pulse),
    ]


def bar(turn: Turn, target: float) -> list[Seg]:
    total = turn.total_s or 0.0
    k = int(total / target * BAR_N + 0.5)
    inside, out_k = min(k, BAR_N), min(BAR_X, max(0, k - BAR_N))
    if turn.over_target:
        filled = [S("█" * inside, "wa")]
    else:  # within target: the accent gradient, one step per cell of the full bar
        filled = [S("█", gradient_class(i, BAR_N)) for i in range(inside)]
    return filled + [S("░" * (BAR_N - inside), "dm"), S("┊", "t2"), S("█" * out_k, "wa")]


def stage_cells(turn: Turn) -> list[Seg]:
    out: list[Seg] = []
    for name in STAGES:
        stage = turn.stage(name)
        if stage.state is StageState.FAILED:
            out.append(S("✗".rjust(STAGE_W), "er b"))
        elif stage.state is StageState.DONE and stage.ms is not None:
            out.append(S(f"{stage.ms:.0f}".rjust(STAGE_W), "t1"))
        else:
            out.append(S("·".rjust(STAGE_W), "mu"))
    return out


def turns_header(model: HostModel, g: Grid) -> list[list[Seg]]:
    counts = model.counts()
    extra = [
        [S("▲ ", "wa b"), S(f"{counts.over_target} over target", "wa")],
        [S("✗ ", "er b"), S(f"{counts.failed} failed", "er")],
    ]
    newest = [S("newest first", "mu")]
    title = header("Voice turns", f"{counts.finished} finished", extra)
    first = place(
        [(X_G, rule_to(title, X_G, g.right - width(newest) - 2))], g.w, "bc", right=newest
    )
    cols: list[Item] = [
        (X_T, [S("time", "mu")]), (X_ID, [S("turn", "mu")]), (X_H, [S("heard", "mu")]),
        (g.x_l, [S("lane", "mu")]),
        (g.x_tot, [S("total".rjust(SEC_W), "mu")]), (g.x_ob, [S("over".rjust(SEC_W), "mu")]),
    ]
    if g.x_b is not None:
        cols.append((g.x_b, [S("vs lane target".ljust(BAR_N), "mu"), S("┊", "t2")]))
    if g.x_st is not None:
        cols.append((g.x_st, [S(s.rjust(STAGE_W), "mu") for s in STAGES] + [S("ms".rjust(3), "mu")]))
    return [first, place(cols, g.w, "bc")]


def turn_rows(turn: Turn, g: Grid) -> list[list[Seg]]:
    heard = turn.heard
    lead: list[Item] = [
        (X_T, [S(clock(turn.started_at), "mu")]),
        (X_ID, [S(turn.turn_id[:8] or "?", "t2")]),
        (X_H, [S(clip(heard, g.heard_w), "t1") if heard else S("no transcript", "mu i")]),
    ]
    if g.x_st is not None:
        lead.append((g.x_st, stage_cells(turn)))
    if turn.is_failed:
        reason = f"failed at {turn.error_stage}"
        if turn.error_message:
            reason += f" · {turn.error_message}"
        stop = (g.x_st if g.x_st is not None else g.right + 1) - 1
        items = lead + [(X_G, [S(*FAIL)])]
        if g.x_b is not None:
            start = g.x_b
            items.append((g.x_l, [S("—", "mu")]))
        else:  # no bar column: the reason takes the lane's place
            start = g.x_l
        items.append((start, [S(clip(reason, stop - start), "er b")]))
        return [place(items, g.w, "be")]

    over = turn.over_target
    bg = "bw" if over else "bc"
    target = turn.target_s
    total = turn.total_s
    items = lead + [(X_G, [S(*(OVER if over else OK))]), (g.x_l, [S(clip(turn.lane or "—", 5), "t2")])]
    items.append((g.x_tot, [S(fmt_s(total).rjust(SEC_W), "wa b" if over else "t1")]))
    if target is not None and total is not None:
        if g.x_b is not None:
            items.append((g.x_b, bar(turn, target)))
        if over and turn.over_by_s is not None:
            items.append((g.x_ob, [S(f"+{turn.over_by_s:.1f} s".rjust(SEC_W), "wa b")]))
    elif g.x_b is not None:
        note = "acknowledge only" if turn.lane == "task" else "no target"
        items.append((g.x_b, [S(note, "mu i")]))
    rows = [place(items, g.w, bg)]
    sub: list[Seg] = []
    if turn.actions:
        names = ", ".join(
            action.action_type + ("" if action.value is None else f" {action.value}")
            for action in turn.actions
        )
        sub += [S("→ ", "mu"), S(names, "t2"), S("  ")]
    if turn.reply:
        sub.append(S(turn.reply.replace("\n", " "), "mu"))
    if sub:
        rows.append(place([(X_H, _cut(sub, g.right + 1 - X_H))], g.w, bg))
    return rows


#: Lanes table columns after the turn count, in screen order, and the order in which
#: they are kept when the table is narrow (the verdict glyph stays in every case).
LANE_COLUMNS: Final = ("median", "worst", "target", "verdict")
LANE_KEEP: Final = ("median", "worst", "verdict", "target")


def lanes_rows(model: HostModel, g: Grid) -> list[list[Seg]]:
    w = g.split
    out = [place([(X_G, rule_to(header("Lanes", "finished turns"), X_G, w - 3))], w, "bc")]
    rows: list[tuple[list[Item], dict[str, list[Seg]]]] = []
    for lane in model.lane_stats():
        over = lane.verdict == "over"
        glyph = OVER if over else (OK if lane.verdict == "within" else DOT)
        unit = "turn" if lane.n == 1 else "turns"
        target = fmt_s(lane.target_s) if lane.target_s is not None else "—"
        if lane.verdict == "empty":
            verdict = [S("no turns yet", "mu i")]
        elif lane.verdict == "no_target":
            verdict = [S("acknowledge only", "mu i")]
        elif over:
            verdict = [S(f"{lane.n_over} over by {lane.over_by_s or 0:.1f} s", "wa b")]
        else:
            verdict = [S("all within", "t2")]
        lead: list[Item] = [
            (X_G, [S(*glyph)]), (X_T, [S(lane.lane, "t1 b")]), (X_ID, [S(f"{lane.n} {unit}", "t2")]),
        ]
        columns = {
            "median": [S("median", "mu"), S(" "), S(fmt_s(lane.median_s).rjust(5), "t1")],
            "worst": [S("worst", "mu"), S(" "), S(fmt_s(lane.worst_s).rjust(5), "wa b" if over else "t1")],
            "target": [S("target", "mu"), S(" "),
                       S(target.rjust(5), "t2" if lane.target_s is not None else "mu")],
            "verdict": verdict,
        }
        rows.append((lead, columns))

    # Whole columns only: keep them by priority while they fit, then lay them out in
    # screen order (at full width this is the mockup's grid: 21, 35, 48, 63).
    widths = {name: max(width(cols[name]) for _, cols in rows) for name in LANE_COLUMNS}

    def gap(name: str) -> int:
        return 3 if name == "verdict" else 2

    def span(names: list[str]) -> int:
        shown = [name for name in LANE_COLUMNS if name in names]
        return sum(widths[name] for name in shown) + sum(gap(name) for name in shown[1:])

    kept: list[str] = []
    for name in LANE_KEEP:
        if X_H + span(kept + [name]) <= w - PAD:
            kept.append(name)
    shown = [name for name in LANE_COLUMNS if name in kept]
    for lead, columns in rows:
        items = list(lead)
        x = X_H
        for i, name in enumerate(shown):
            x += gap(name) if i else 0
            items.append((x, columns[name]))
            x += widths[name]
        out.append(place(items, w, "bc"))
    return out


def conversation_rows(model: HostModel, g: Grid) -> list[list[Seg]]:
    w = g.split
    tw = max(8, w - X_ID - PAD)
    out: list[list[Seg]] = []
    messages = model.messages
    if not messages:
        return [place([(X_T, [S("no messages yet", "mu i")])], w, "bc")]
    for i, message in enumerate(messages):
        who = "you" if message.role == "user" else "jarvis"
        live = message.streaming
        lines = [
            line
            for paragraph in (message.text or " ").split("\n")
            for line in (textwrap.wrap(paragraph, tw) or [""])
        ]
        for j, line in enumerate(lines):
            items: list[Item] = []
            if j == 0:
                if live:
                    items.append((X_G, [S(*LIVE)]))
                items.append((X_T, [S(who, "lv b" if live else ("t2 b" if who == "you" else "t1 b"))]))
            body = [S(line, "t2" if who == "you" else "t1")]
            if live and j == len(lines) - 1:
                body.append(S("▍", "lv"))
            items.append((X_ID, body))
            out.append(place(items, w, "bc"))
        if who == "jarvis" and i < len(messages) - 1:
            out.append(blank(w, "bc"))
    return out


def rail_rows(model: HostModel, g: Grid, n: int) -> list[list[Seg]]:
    """The agent card, ``n`` rows tall: agent facts, then its PC actions."""

    x0, w = g.split, g.w - g.split
    xr = x0 + PAD
    xv = xr + 14

    def inside(items: Sequence[Item], right: list[Seg] | None = None) -> list[Seg]:
        return sides(place(items, w, "bc", x0, right=right, edge=g.right), 0, w - 1)

    def kv(key: str, value: list[Seg]) -> list[Seg]:
        return inside([(xr, [S(key, "mu")]), (xv, value)])

    agent = model.agent_status  # "offline" while the daemon link is down, as in the user view
    glyph, cls = state_glyph(agent)
    state = agent.replace("_", " ").upper() or "UNKNOWN"
    queue = [S(f"{model.queue_total} queued", "t1")] if model.queue_total else [S("empty", "mu")]
    body = [
        kv("state", [S(glyph[0] + " ", glyph[1]), S(state, cls)]),
        kv("model", [S(model.agent_model, "t1")] if model.agent_model else [S("—", "mu")]),
        kv("chat", [S(model.chat_title, "t1")] if model.chat_title else [S("—", "mu")]),
        kv("queue", queue),
    ]
    if model.agent_notice:
        # The daemon's own message (e.g. no AI account), whole, wrapped to the card.
        room = max(1, g.right + 1 - xv)
        for i, line in enumerate(textwrap.wrap(model.agent_notice, room) or [""]):
            body.append(kv("needs" if i == 0 else "", [S(line, "er")]))
    body.append(inside([]))
    activities = model.activities
    body.append(inside([(xr, header("PC actions", str(len(activities))))]))
    if not activities:
        body.append(inside([(xr, [S("nothing yet", "mu i")])]))
    for activity in activities:
        running = activity.status == "running"
        mark = {"running": LIVE, "done": OK, "failed": FAIL}.get(activity.status, DOT)
        room = g.right + 1 - len(activity.status) - 1 - xv
        body.append(
            inside(
                [(xr, [S(*mark)]), (xr + 2, [S(clip(activity.kind, 11), "t2")]),
                 (xv, [S(clip(activity.title, room), "t1" if running else "t2")])],
                right=[S(activity.status, "lv" if running else "mu")],
            )
        )
    top = [blank(w, "bc"), card_top(0, w - 1, [S("Agent", "t2 b")])]
    if n < 3:
        return (top + body)[:n]
    rows = top + body[: n - 3]
    rows += [inside([])] * (n - 1 - len(rows))
    return rows + [card_bottom(0, w - 1)]


def render_rows(model: HostModel, w: int, h: int) -> list[list[Seg]]:
    """All rows of the developer view for a ``w`` x ``h`` area (input and footer excluded)."""

    if w <= 0 or h <= 0:
        return []
    g = grid(w)
    rows: list[list[Seg]] = [device_row(model, g)]
    rows += live_band(model, g)
    rows += [blank(w, "bc")]
    rows += turns_header(model, g)

    lower_min = min(LOWER_FULL, max(0, (h - len(rows)) // 2))
    budget = max(1, h - len(rows) - lower_min)
    finished = model.finished_turns()
    if not finished:
        rows.append(place([(X_H, [S("no voice turns yet", "mu i")])], w, "bc"))
    for turn in reversed(finished):
        block = turn_rows(turn, g)
        if len(block) > budget:
            break
        rows += block
        budget -= len(block)

    lower = h - len(rows)
    if lower > 0:
        top = [blank(g.split, "bc")] + lanes_rows(model, g)
        conv_head = [
            place([(X_G, rule_to(header("Conversation", "typed"), X_G, g.split - 3))], g.split, "bc")
        ]
        room = lower - len(top) - 1 - len(conv_head)
        conv = conversation_rows(model, g)[-room:] if room > 0 else []
        filler = max(0, lower - len(top) - len(conv_head) - len(conv))
        left = (top + [blank(g.split, "bc")] * filler + conv_head + conv)[:lower]
        left += [blank(g.split, "bc")] * (lower - len(left))
        rail = rail_rows(model, g, lower)
        rows += [a + b for a, b in zip(left, rail)]
    return rows[:h]


def _keys(keys: Sequence[tuple[str, str]], sep: str) -> list[Seg]:
    segs: list[Seg] = []
    for key, action in keys:
        if segs:
            segs.append(S(sep))
        segs += [S(key, "t2 b"), S(" " + action, "mu")]
    return segs


def _legend(entries: Sequence[tuple[Seg, str]], sep: str) -> list[Seg]:
    segs: list[Seg] = []
    for glyph, word in entries:
        segs += ([S(sep)] if segs else []) + [S(glyph[0] + " ", glyph[1]), S(word, "mu")]
    return segs


def footer_row(w: int, keys: Sequence[tuple[str, str]], legend: bool) -> list[Seg]:
    """Keys at column 2 (bold key, muted label); the glyph legend right-aligned if asked.

    The spacing is chosen for the developer view's legend, so both views show the same
    keys at the same width: wide spacing when the whole legend fits, tighter spacing
    next, and only then whole legend entries go, the least telling first.
    """

    def room(segs: list[Seg]) -> int:
        return w - PAD * 2 - width(segs) - 2

    found: list[Seg] = []
    for key_sep, legend_sep in (("   ", "   "), ("   ", "  "), ("  ", "  ")):
        segs = _keys(keys, key_sep)
        found = _legend(LEGEND, legend_sep)
        if width(found) <= room(segs):
            break
    else:
        entries = list(LEGEND)
        for word in LEGEND_DROP_ORDER:
            found = _legend(entries, "  ")
            if width(found) <= room(segs):
                break
            entries = [entry for entry in entries if entry[1] != word]
        else:
            found = []
    return place([(X_G, segs)], w, "bc", right=found if legend and found else None)


class DeveloperView(Widget):
    """The developer view, redrawn from the model on every refresh."""

    DEFAULT_CSS = """
    DeveloperView {
        height: 1fr;
        background: $jarvis-background;
    }
    """

    def __init__(self, model: HostModel, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.model = model
        self.palette: Theme = THEMES[DEFAULT_DARK]

    def render(self) -> Text:
        return to_text(render_rows(self.model, self.size.width, self.size.height), self.palette)
