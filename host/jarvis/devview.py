# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""Developer view: the approved mockup ``design/tui-developer/06-pipeline-refined.html``.

Every row is laid out on one shared column grid, as in the mockup generator
(``design/tui-developer/.preview/opt6.py``): glyph, time or name, turn id, heard text,
then lane, bar against the lane target, total, over-by and four stage columns anchored
to the right edge. Colour marks state only: live (accent), within (green), over
target (yellow) and failed (red). Everything comes from ``HostModel``; with no data
each region shows an empty state instead of demo figures.

At 140 columns the grid is the mockup's. Narrower windows drop the stage columns and
then the bars; fewer rows shrink the turn table first.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from .client import ConnectionStatus
from .model import STAGES, HostModel, StageState, Turn

# Palette of the mockup (design/tui-developer/.preview/lib.py), AA contrast checked there.
PALETTE: Final = {
    "bc": "#19141d",  # canvas
    "bs": "#251d2b",  # surface
    "br": "#201926",  # rail
    "bw": "#2b2320",  # over-target tint
    "be": "#331d27",  # failed tint
    "bd": "#4a3d54",  # border lines
    "t1": "#f3ebf5",
    "t2": "#cdc0d3",
    "mu": "#a898b0",
    "ac": "#f4a873",
    "ok": "#97d4a4",
    "wa": "#ecd27e",
    "er": "#f7889a",
}
BACKGROUNDS: Final = frozenset({"bc", "bs", "br", "bw", "be"})

Seg = tuple[str, str]
Item = tuple[int, list[Seg]]

LIVE: Final[Seg] = ("◐", "ac")
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
QUIET_STATES: Final = frozenset({"starting", "restarting", "unknown"})
LEGEND: Final = (
    (("●", "ok"), "connected"),
    (LIVE, "live"),
    (WAIT, "waiting"),
    (OK, "within"),
    (OVER, "over target"),
    (FAIL, "failed"),
    (("┊", "t2"), "lane target"),
)
KEYS: Final = (("enter", "send"), ("ctrl+a", "stop"), ("ctrl+q", "quit"), ("f2", "user view"))


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


def _style(cls: str) -> Style:
    parts = cls.split()
    fg = next((PALETTE[p] for p in parts if p in PALETTE and p not in BACKGROUNDS), None)
    bg = next((PALETTE[p] for p in parts if p in BACKGROUNDS), None)
    return Style(color=fg, bgcolor=bg, bold="b" in parts, italic="i" in parts)


def to_text(rows: Sequence[Sequence[Seg]]) -> Text:
    text = Text(no_wrap=True, overflow="crop", end="")
    for index, row in enumerate(rows):
        if index:
            text.append("\n")
        for chunk, cls in row:
            text.append(chunk, _style(cls))
    return text


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
    return LIVE, "ac b"


# --- Regions --------------------------------------------------------------------


def device_row(model: HostModel, g: Grid) -> list[Seg]:
    items: list[Item] = []
    devices = list(model.devices.values())
    if not devices:
        items.append((X_G, [S("no device yet", "mu i")]))
    else:
        device = devices[0]
        items.append((X_G, [S(device.device_id, "t1 b")]))
        glyph, cls = state_glyph(device.state)
        items.append((X_ID, [S(glyph[0] + " ", glyph[1]), S(device.state.upper(), cls)]))
        if device.rssi is not None:
            items.append((X_H + 2, [S(f"{device.rssi} dBm", "t2")]))
        if device.ip:
            items.append((X_H + 12, [S(device.ip, "t2")]))
        if device.online:
            items.append((X_H + 28, [S("● ", "ok"), S("online", "t2")]))
        else:
            items.append((X_H + 28, [S("✗ ", "er b"), S("offline", "t2")]))
        if len(devices) > 1:
            items.append((X_H + 38, [S(f"+{len(devices) - 1} more", "mu")]))
    right = link("daemon", model.daemon_status) + [S("   ")] + link("backend", model.backend_status)
    return place(items, g.w, "bs", right=right)


def live_band(model: HostModel, g: Grid) -> list[list[Seg]]:
    turn = model.live_turn()
    medians = model.stage_medians()
    space = g.right + 1 - X_H
    arw = 7 if (space - 21) // 4 >= 18 else 3
    boxw = max(8, (space - 3 * arw) // 4)
    xs = [X_H + i * (boxw + arw) for i in range(4)]
    arrow = "  ──▸  " if arw == 7 else " ▸ "

    def box(i: int, kind: str) -> list[Seg]:
        name = STAGES[i]
        stage = turn.stage(name) if turn else None
        state = stage.state if stage else None
        live = state is StageState.LIVE
        border = "ac" if live else "bd"
        if kind == "top":
            return [S("┌" + "─" * (boxw - 2) + "┐", border)]
        if kind == "bot":
            return [S("└" + "─" * (boxw - 2) + "┘", border)]
        if live:
            glyph, label, word = LIVE, "ac b", [S(LIVE_WORDS[name], "ac")]
        elif state is StageState.DONE and stage is not None:
            ms = "?" if stage.ms is None else f"{stage.ms:.0f}"
            glyph, label, word = OK, "t2", [S(ms, "t1"), S(" ms", "mu")]
        elif turn is None:
            glyph, label, word = WAIT, "t2", [S("—", "mu")]
        else:
            glyph, label, word = WAIT, "t2", [S("waiting", "mu")]
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
        return place(items, g.w, "bs")

    if turn is None:
        mid: list[Item] = [(X_G, [S(*WAIT)]), (X_T, [S("idle", "mu")])]
        bot: list[Item] = [(X_G, [S("no live turn", "mu i")])]
    else:
        mid = [(X_G, [S(*LIVE)]), (X_T, [S(clock(turn.started_at), "mu")]),
               (X_ID, [S(turn.turn_id[:8] or "?", "t1 b")])]
        bot = [(X_G, [S("lane — until done", "mu i")])]
    return [
        row([(X_G, header("LIVE TURN"))], "top"),
        row(mid, "mid"),
        row(bot, "bot"),
        row([(X_G, [S("stage median", "mu")])], "med"),
    ]


def bar(turn: Turn, target: float) -> list[Seg]:
    total = turn.total_s or 0.0
    k = int(total / target * BAR_N + 0.5)
    inside, out_k = min(k, BAR_N), min(BAR_X, max(0, k - BAR_N))
    fill = "wa" if turn.over_target else "mu"
    return [S("█" * inside, fill), S("░" * (BAR_N - inside), "bd"), S("┊", "t2"), S("█" * out_k, "wa")]


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
    first = place(
        [(X_G, header("VOICE TURNS", f"{counts.finished} finished", extra))],
        g.w, "bc", right=[S("newest first", "mu")],
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
        sub += [S("▸ ", "mu"), S(names, "t2"), S("  ")]
    if turn.reply:
        sub.append(S(turn.reply.replace("\n", " "), "mu"))
    if sub:
        rows.append(place([(X_H, _cut(sub, g.right + 1 - X_H))], g.w, bg))
    return rows


def lanes_rows(model: HostModel, g: Grid) -> list[list[Seg]]:
    w = g.split
    out = [place([(X_G, header("LANES", "finished turns"))], w, "bc")]
    for lane in model.lane_stats():
        over = lane.verdict == "over"
        glyph = OVER if over else (OK if lane.verdict == "within" else DOT)
        unit = "turn" if lane.n == 1 else "turns"
        target = fmt_s(lane.target_s) if lane.target_s is not None else "—"
        items: list[Item] = [
            (X_G, [S(*glyph)]), (X_T, [S(lane.lane, "t1 b")]), (X_ID, [S(f"{lane.n} {unit}", "t2")]),
            (X_H, [S("median", "mu")]), (X_H + 7, [S(fmt_s(lane.median_s).rjust(5), "t1")]),
            (X_H + 14, [S("worst", "mu")]),
            (X_H + 20, [S(fmt_s(lane.worst_s).rjust(5), "wa b" if over else "t1")]),
            (X_H + 27, [S("target", "mu")]),
            (X_H + 34, [S(target.rjust(5), "t2" if lane.target_s is not None else "mu")]),
        ]
        if lane.verdict == "empty":
            verdict = [S("no turns yet", "mu i")]
        elif lane.verdict == "no_target":
            verdict = [S("acknowledge only", "mu i")]
        elif over:
            verdict = [S(f"{lane.n_over} over by {lane.over_by_s or 0:.1f} s", "wa b")]
        else:
            verdict = [S("all within", "t2")]
        items.append((X_H + 42, verdict))
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
                items.append((X_T, [S(who, "ac b" if live else ("t2 b" if who == "you" else "t1 b"))]))
            body = [S(line, "t2" if who == "you" else "t1")]
            if live and j == len(lines) - 1:
                body.append(S("▍", "ac"))
            items.append((X_ID, body))
            out.append(place(items, w, "bc"))
        if who == "jarvis" and i < len(messages) - 1:
            out.append(blank(w, "bc"))
    return out


def rail_rows(model: HostModel, g: Grid) -> list[list[Seg]]:
    x0, w = g.split, g.w - g.split
    xr = x0 + PAD
    xv = xr + 14

    def kv(key: str, value: list[Seg]) -> list[Seg]:
        return place([(xr, [S(key, "mu")]), (xv, value)], w, "br", x0)

    glyph, cls = state_glyph(model.agent_state)
    state = model.agent_state.replace("_", " ").upper() or "UNKNOWN"
    queue = [S(f"{model.queue_total} queued", "t1")] if model.queue_total else [S("empty", "mu")]
    rows = [
        blank(w, "br"),
        place([(xr, header("AGENT"))], w, "br", x0),
        kv("state", [S(glyph[0] + " ", glyph[1]), S(state, cls)]),
        kv("model", [S(model.agent_model, "t1")] if model.agent_model else [S("—", "mu")]),
        kv("chat", [S(model.chat_title, "t1")] if model.chat_title else [S("—", "mu")]),
        kv("queue", queue),
        blank(w, "br"),
    ]
    activities = model.activities
    rows.append(place([(xr, header("PC ACTIONS", str(len(activities))))], w, "br", x0))
    if not activities:
        rows.append(place([(xr, [S("nothing yet", "mu i")])], w, "br", x0))
    for activity in activities:
        running = activity.status == "running"
        mark = {"running": LIVE, "done": OK, "failed": FAIL}.get(activity.status, DOT)
        room = g.right + 1 - len(activity.status) - 1 - xv
        rows.append(
            place(
                [(xr, [S(*mark)]), (xr + 2, [S(clip(activity.kind, 11), "t2")]),
                 (xv, [S(clip(activity.title, room), "t1" if running else "t2")])],
                w, "br", x0, right=[S(activity.status, "ac" if running else "mu")], edge=g.right,
            )
        )
    return rows


def render_rows(model: HostModel, w: int, h: int) -> list[list[Seg]]:
    """All rows of the developer view for a ``w`` x ``h`` area (input and footer excluded)."""

    if w <= 0 or h <= 0:
        return []
    g = grid(w)
    rows: list[list[Seg]] = [device_row(model, g), blank(w, "bs")]
    rows += live_band(model, g)
    rows += [blank(w, "bs"), blank(w, "bc")]
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
        conv_head = [place([(X_G, header("CONVERSATION", "typed"))], g.split, "bc")]
        room = lower - len(top) - 1 - len(conv_head)
        conv = conversation_rows(model, g)[-room:] if room > 0 else []
        filler = max(0, lower - len(top) - len(conv_head) - len(conv))
        left = (top + [blank(g.split, "bc")] * filler + conv_head + conv)[:lower]
        left += [blank(g.split, "bc")] * (lower - len(left))
        rail = rail_rows(model, g)[:lower]
        rail += [blank(w - g.split, "br")] * (lower - len(rail))
        rows += [a + b for a, b in zip(left, rail)]
    return rows[:h]


def footer_row(w: int) -> list[Seg]:
    keys: list[Seg] = []
    for key, action in KEYS:
        if keys:
            keys.append(S("   "))
        keys += [S(key, "t2 b"), S(" " + action, "mu")]
    room = w - PAD * 2 - width(keys) - 2
    legend: list[Seg] = []
    for sep in ("   ", "  "):
        legend = []
        for glyph, word in LEGEND:
            part = ([S(sep)] if legend else []) + [S(glyph[0] + " ", glyph[1]), S(word, "mu")]
            if width(legend) + width(part) > room:
                break
            legend += part
        if len(legend) >= 3 * len(LEGEND) - 1:
            break
    return place([(X_G, keys)], w, "bc", right=legend or None)


class DeveloperView(Widget):
    """The developer view, redrawn from the model on every refresh."""

    DEFAULT_CSS = """
    DeveloperView {
        height: 1fr;
        background: #19141d;
    }
    """

    def __init__(self, model: HostModel, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.model = model

    def render(self) -> Text:
        return to_text(render_rows(self.model, self.size.width, self.size.height))


class DeveloperFooter(Widget):
    """Keys and the glyph legend of the developer view."""

    DEFAULT_CSS = """
    DeveloperFooter {
        height: 1;
        background: #19141d;
    }
    """

    def render(self) -> Text:
        return to_text([footer_row(self.size.width)])
