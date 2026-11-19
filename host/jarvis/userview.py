# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""User view: the approved mockup ``design/tui-user/08-story-chat-polished.html``, in the
unified conventions of the theme artboards (``design/tui-theme``).

A header of two rounded cards: Jarvis (its state in large block letters with the accent
gradient, a spinner, what it is working on, how many messages wait, a pulse on the
card's bottom edge) and the voice device (state, a hint, a level meter, connection and
signal). Below, the day's exchanges on a timeline rail: time and said/typed beside a
thin line that turns into the live accent bar, the user's words right-aligned in a
bubble, Jarvis's answer under ``◆``, and what it did "on your PC" under that answer.

Everything comes from ``HostModel``. Voice exchanges are voice turns (heard text,
reply, catalogue actions, failure); typed exchanges are the daemon's user messages,
the answer that follows and its activity. When the agent behind a voice turn is Gentle
Dot, the daemon echoes the heard text as a ``user_message``; that echo folds into the
voice exchange instead of showing twice. No developer detail is shown here: no turn
ids, timings, stage or lane names, signal in dBm or addresses.

The voice meter is drawn, not measured: contract C2 carries no audio level, so it
moves while the device listens or speaks and lies flat otherwise.
"""

from __future__ import annotations

import math
import re
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from rich.text import Text
from textual.timer import Timer
from textual.widget import Widget

from .backend import Device
from .client import ConnectionStatus
from .devview import S, Seg, blank, clip, clock, merge, width
from .model import OFFLINE, STAGES, HostModel, Message, StageState, Turn
from .theme import BACKGROUND, DEFAULT_DARK, THEMES, Theme, gradient_class, to_text

#: Seconds within which a daemon user message that repeats a voice turn's words is
#: taken as that turn's echo.
ECHO_WINDOW_S: Final = 120.0
#: Seconds between animation frames (spinner, card pulse, voice meter).
FRAME_S: Final = 0.15
SPINNER: Final = "◐◓◑◒"
BUSY: Final = frozenset({"thinking", "working", "needs_you"})
ACTIVE_DEVICE: Final = frozenset({"recording", "listening", "speaking"})
WORDS: Final = {
    "idle": "READY",
    "thinking": "THINKING",
    "working": "WORKING",
    "needs_you": "NEEDS YOU",
    "starting": "STARTING",
    "restarting": "STARTING",
    "error": "ERROR",
}
STAGE_WORDS: Final = {"stt": "LISTENING", "route": "THINKING", "agent": "THINKING", "tts": "SPEAKING"}
#: What a live voice turn is doing, in the thread, by its current stage.
DOING: Final = {"stt": "Listening…", "route": "Thinking…", "agent": "Thinking…", "tts": "Speaking…"}
ON_PC: Final = "on your PC  "

#: One header fact: complete copies of the same message, longest first.
Copy = tuple[str, ...]
#: Why Gentle Dot is offline (``HostModel.agent_offline_reason``): the problem, then
#: what to do, each a complete sentence that fits the card at 140 columns.
OFFLINE_COPY: Final[dict[str, tuple[Copy, Copy]]] = {
    "no_token": (("Gentle Dot isn't set up yet",), ("start it once to type here",)),
    "refused": (("Gentle Dot refused this window",), ("restart both to type here",)),
    "mismatch": (("Gentle Dot needs updating",), ("update both to type here",)),
    "not_running": (("Gentle Dot is not running",), ("start it to type here",)),
}


def pick(copy: Copy, room: int) -> str | None:
    """The longest complete copy that fits ``room`` cells, if any."""

    return next((text for text in copy if len(text) <= room), None)

# --- Exchanges ------------------------------------------------------------------

Did = tuple[str, str]  # (done | live | background | failed, what)


@dataclass(slots=True)
class Exchange:
    """One thing the user said or typed and what Jarvis answered and did."""

    source: str  # voice | typed
    at: float | None
    said: str | None
    answer: str
    did: list[Did] = field(default_factory=list)
    live: bool = False
    #: What a live exchange is doing before its answer starts ("Thinking…").
    doing: str | None = None
    #: (what went wrong, what to do now, longer guidance) when it failed.
    failure: tuple[str, str, str] | None = None
    stopped: bool = False


def _words(text: str) -> str:
    return re.sub(r"[\s.,;:!?¿¡…]+", " ", text.casefold()).strip()


def describe_action(action_type: str, value: object) -> str:
    """A catalogue action (contract C4) in plain words."""

    value_text = "" if value is None else str(value)
    if action_type == "media_key":
        return {
            "play_pause": "played or paused the music",
            "next": "skipped to the next track",
            "previous": "went back a track",
        }.get(value_text, "pressed a media key")
    if action_type == "volume":
        return {
            "up": "turned the volume up",
            "down": "turned the volume down",
            "mute": "muted the sound",
        }.get(value_text, "changed the volume")
    if action_type == "open_app":
        return f"opened {value_text.replace('-', ' ').replace('_', ' ').title()}" if value_text else "opened an app"
    words = action_type.replace("_", " ").strip() or "did something"
    return f"{words} {value_text}".strip()


def _voice_failure(turn: Turn, model: HostModel) -> tuple[str, str, str]:
    device = model.devices.get(turn.device_id)
    listening = device is not None and device.online
    again = (
        "the voice device is still listening, so just talk again"
        if listening
        else "the voice device is not connected; check its power and wifi, then talk again"
    )
    if turn.error_stage == "stt":
        return "Couldn't hear you.", "Say it again.", again
    if turn.error_stage == "tts":
        return "Couldn't say the answer out loud.", "Ask again.", again
    return "Couldn't get an answer.", "Try again in a moment.", again


def _typed_failure(message: Message) -> tuple[str, str, str]:
    return (
        "Couldn't finish the answer.",
        "Send it again.",
        "type it again below; if it keeps failing, check the Gentle Dot app",
    )


def _activity_did(status: str, title: str) -> Did:
    state = {"running": "live", "done": "done", "failed": "failed"}.get(status, "background")
    return state, title


def exchanges(model: HostModel) -> list[Exchange]:
    """Voice and typed exchanges, oldest first, a voice turn's daemon echo folded in."""

    voice: list[tuple[Turn, Exchange]] = []
    for turn in model.turns:
        exchange = Exchange(
            source="voice",
            at=turn.started_at,
            said=turn.heard,
            answer=turn.reply or "",
            did=[("live" if turn.is_live else "done", describe_action(a.action_type, a.value)) for a in turn.actions],
            live=turn.is_live,
        )
        if turn.is_failed:
            exchange.failure = _voice_failure(turn, model)
        if turn.is_live:
            stage = next((s for s in STAGES if turn.stage(s).state is StageState.LIVE), "agent")
            exchange.doing = DOING[stage]
        voice.append((turn, exchange))

    claimed: set[str] = set()

    def echo_of(message: Message) -> Exchange | None:
        if message.at is None:
            return None
        said = _words(message.text)
        for turn, exchange in voice:
            if (
                turn.turn_id not in claimed
                and turn.heard
                and _words(turn.heard) == said
                and abs(message.at - turn.started_at) <= ECHO_WINDOW_S
            ):
                claimed.add(turn.turn_id)
                return exchange
        return None

    typed: list[Exchange] = []
    current: Exchange | None = None
    echo = False
    for message in model.messages:
        if message.role == "user":
            found = echo_of(message)
            echo = found is not None
            if found is None:
                found = Exchange(source="typed", at=message.at, said=message.text, answer="")
                typed.append(found)
            current = found
            current.did += [_activity_did(a.status, a.title) for a in model.activities_of(message.message_id)]
            continue
        if current is None:  # an answer without its question (start of the history page)
            current = Exchange(source="typed", at=message.at, said=None, answer="")
            typed.append(current)
            echo = False
        answer = message.answer
        if not (echo and current.answer):  # a voice turn's own reply wins over its echo's
            current.answer = f"{current.answer}\n\n{answer}".strip() if current.answer else answer
        current.did += [_activity_did(a.status, a.title) for a in model.activities_of(message.message_id)]
        if message.streaming:
            current.live = True
        if message.error:
            current.failure = _typed_failure(message)
        current.stopped = current.stopped or message.stopped

    # A typed question still waiting for its answer is live while the agent works.
    if typed and model.agent_state in BUSY and not any(e.live for e in typed):
        last = typed[-1]
        if last.failure is None and model.messages and model.messages[-1].role == "user":
            last.live = True

    everything = [exchange for _, exchange in voice] + typed
    # History has no time: it goes first, in its own order; the rest by time.
    undated = [e for e in typed if e.at is None]
    dated = sorted((e for e in everything if e.at is not None), key=lambda e: e.at or 0.0)
    return undated + dated


# --- Big letters ----------------------------------------------------------------

# 5-pixel-tall letters packed two pixel rows per cell with half blocks (as in the mockup).
PIX: Final = {
    "A": [".###.", "#...#", "#####", "#...#", "#...#"],
    "C": [".####", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "####.", "#....", "#####"],
    "F": ["#####", "#....", "####.", "#....", "#...."],
    "G": [".###.", "#....", "#..##", "#...#", ".###."],
    "H": ["#...#", "#...#", "#####", "#...#", "#...#"],
    "I": ["###", ".#.", ".#.", ".#.", "###"],
    "K": ["#...#", "#..#.", "###..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#####"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "####.", "#....", "#...."],
    "R": ["####.", "#...#", "####.", "#..#.", "#...#"],
    "S": [".####", "#....", ".###.", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#.#.#", "##.##", "#...#"],
    "Y": ["#...#", ".#.#.", "..#..", "..#..", "..#.."],
    " ": ["..", "..", "..", "..", ".."],
}
HALF: Final = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}


def big(word: str) -> list[str]:
    """Three rows of block letters; letters without a glyph are skipped."""

    letters = [PIX[c] for c in word.upper() if c in PIX]
    if not letters:
        return ["", "", ""]
    rows = ["." * 99] + [".".join(letter[r] for letter in letters) for r in range(5)]
    return [
        "".join(HALF[(a[i] == "#", b[i] == "#")] for i in range(len(rows[1])))
        for a, b in ((rows[0], rows[1]), (rows[2], rows[3]), (rows[4], rows[5]))
    ]


def gradient(text: str, extra: str = "") -> list[Seg]:
    n = len(text)
    return [S(ch, f"{gradient_class(i, n)} {extra}".strip()) for i, ch in enumerate(text)]


# --- Voice meter ----------------------------------------------------------------


def meter(cells: int, frame: int, active: bool) -> tuple[str, str]:
    """Two rows (top, bottom) of a speech-like level meter in eighth blocks."""

    if cells <= 0:
        return "", ""
    if not active:
        return " " * cells, "▁" * cells
    seed = 0.6 + frame * 0.45
    hi, lo = [], []
    for x in range(cells):
        u = x / cells
        env = 0.2 + 0.8 * (math.sin(math.pi * u) ** 1.2)
        syl = abs(math.sin(u * 7.1 + seed)) * 0.6 + abs(math.sin(u * 19.3 + 1.7 + seed * 0.5)) * 0.4
        h = max(1, round(16 * env * syl))
        lo.append(" ▁▂▃▄▅▆▇█"[min(h, 8)])
        hi.append(" ▁▂▃▄▅▆▇█"[max(0, h - 8)])
    return "".join(hi), "".join(lo)


# --- Row helpers ----------------------------------------------------------------


def region(segs: Sequence[Seg], w: int, bg: str = "bc") -> list[Seg]:
    """``segs`` cut or padded to exactly ``w`` cells, on background ``bg``."""

    out: list[Seg] = []
    left = w
    for text, cls in segs:
        if left <= 0:
            break
        part = text[:left]
        has_bg = any(code in BACKGROUND for code in cls.split())
        out.append((part, cls if has_bg else f"{cls} {bg}".strip()))
        left -= len(part)
    if left > 0:
        out.append((" " * left, bg))
    return merge(out)


def top_border(title: list[Seg], w: int) -> list[Seg]:
    segs = [S("╭─", "bd"), S(" ")] + title + [S(" ")]
    if width(segs) + 1 > w:
        return region([S("╭", "bd")] + title[:1], w - 1) + [S("╮", "bd")] if w > 1 else [S("╮", "bd")]
    return segs + [S("─" * (w - 1 - width(segs)), "bd"), S("╮", "bd")]


def bottom_border(w: int, pulse: tuple[int, int] | None = None, subtitle: list[Seg] | None = None) -> list[Seg]:
    if w < 2:
        return [S("╯"[:w], "bd")]
    if subtitle:
        tail = [S(" ")] + subtitle + [S(" "), S("──╯", "bd")]
        if width(tail) + 1 <= w:
            return [S("╰" + "─" * (w - 1 - width(tail)), "bd")] + tail
    out: list[Seg] = [S("╰", "bd")] + [S("─", "bd")] * (w - 2) + [S("╯", "bd")]
    if pulse is not None:
        at, n = pulse
        for j in range(n):
            if 0 < at + j < w - 1:
                out[at + j] = S("━", gradient_class(j, n))
    return merge(out)


def side(inner: Sequence[Seg], w: int) -> list[Seg]:
    if w < 2:
        return region([], w)
    return [S("│", "bd")] + region(inner, w - 2) + [S("│", "bd")]


# --- Header ---------------------------------------------------------------------


def _first_device(model: HostModel) -> Device | None:
    devices = list(model.devices.values())
    online = [d for d in devices if d.online]
    return (online or devices or [None])[0]


def jarvis_status(model: HostModel, live: Exchange | None) -> tuple[str, str, Copy, Copy]:
    """(word, mood, what, waiting); mood: busy, ok, failed, quiet or offline.

    The agent's availability is ``HostModel.agent_status``, the same state the
    developer view shows: with the daemon link down Jarvis is OFFLINE, never READY.
    """

    n = model.queue_total
    waiting: Copy = (
        ("nothing waiting",)
        if n == 0
        else ("1 more message waiting its turn", "1 more waiting")
        if n == 1
        else (f"{n} more messages waiting their turn", f"{n} more waiting")
    )
    voice = model.live_turn()
    voice_word = None
    if voice is not None:
        stage = next((s for s in STAGES if voice.stage(s).state is StageState.LIVE), "stt")
        voice_word = STAGE_WORDS[stage]
    state = model.agent_status
    if state != OFFLINE:
        if state in BUSY:
            if state == "needs_you":
                what: Copy = ("waiting for you in the Gentle Dot app", "needs you in Gentle Dot")
            elif live is not None and live.source == "voice":
                what = ("on what you said",)
            elif live is not None:
                what = ("on the message you typed", "on your message")
            else:
                what = ("on a task",)
            return WORDS[state], "busy", what, waiting
        if voice_word:
            return voice_word, "busy", ("on what you said",), waiting
        if state == "idle":
            return "READY", "ok", ("talk or type whenever you like", "talk or type"), waiting
        if state == "error":
            return ("ERROR", "failed", ("something went wrong in Gentle Dot", "Gentle Dot hit an error"),
                    ("restart it if this stays", "restart it"))
        return WORDS.get(state, "STARTING"), "quiet", ("getting ready",), waiting
    if voice_word:
        return (voice_word, "busy", ("on what you said",),
                ("typing needs Gentle Dot running", "typing needs Gentle Dot", "can't type for now"))
    if model.backend_status is not ConnectionStatus.CONNECTED:
        return ("OFFLINE", "offline", ("can't reach Jarvis or Gentle Dot", "can't reach anything"),
                ("this window reconnects on its own", "reconnecting on its own"))
    reason = model.agent_offline_reason or "not_running"
    what, todo = OFFLINE_COPY[reason]
    return "OFFLINE", "failed" if reason in ("refused", "mismatch") else "offline", what, todo


def device_status(model: HostModel) -> tuple[Seg, str, str, bool]:
    """(glyph, state word, hint, meter moving)."""

    device = _first_device(model)
    if device is None:
        return ("○", "mu"), "No voice device yet", "turn it on to talk to Jarvis", False
    if not device.online:
        return ("○", "mu"), "Offline", "check its power and wifi", False
    state = device.state
    if state == "idle":
        return ("●", "ok"), "Ready", "talk any time", False
    if state in ("recording", "listening"):
        return ("◎", "lv b"), "Listening", "talk any time", True
    if state == "thinking":
        return ("◐", "lv"), "Thinking", "one moment", False
    if state == "speaking":
        return ("◐", "lv"), "Speaking", "answering out loud", True
    if state == "error":
        return ("✗", "er b"), "Needs attention", "restart the voice device", False
    return ("○", "mu"), state.replace("_", " ").capitalize(), "", False


def connection(model: HostModel) -> list[Seg]:
    if model.backend_status is not ConnectionStatus.CONNECTED:
        return [S("○", "mu"), S(" can't reach the voice service", "t2")]
    device = _first_device(model)
    if device is None:
        return [S("○", "mu"), S(" waiting for a device", "t2")]
    if not device.online:
        return [S("○", "mu"), S(" not connected", "t2")]
    out = [S("●", "ok"), S(" connected to this PC", "t2")]
    if device.rssi is not None:
        rssi = device.rssi
        n, word = (4, "good") if rssi >= -60 else (3, "fair") if rssi >= -70 else (2, "weak") if rssi >= -80 else (1, "poor")
        bars = [S(ch, gradient_class(j, 4) if j < n else "dm") for j, ch in enumerate("▂▄▆█")]
        out += [S("  ·  ", "mu")] + bars + [S(f" {word} signal", "t2")]
    return out


def is_animated(model: HostModel) -> bool:
    return jarvis_status(model, None)[1] == "busy" or device_status(model)[3]


def jarvis_card(model: HostModel, live: Exchange | None, w: int, frame: int) -> list[list[Seg]]:
    word, mood, what, waiting = jarvis_status(model, live)
    inner = w - 2
    if mood == "busy":
        glyph: Seg = S(SPINNER[frame % len(SPINNER)], "lv b")
    elif mood == "ok":
        glyph = S("●", "ok")
    elif mood == "failed":
        glyph = S("✗", "er b")
    else:
        glyph = S("○", "mu")
    letters = big(word)
    bw = len(letters[0])
    fact_x = 6 + bw + 4
    room = inner - fact_x - 5  # after "│   ", one cell before the card edge
    if mood == "failed":
        paint = lambda t: [S(t, "er b")]  # noqa: E731
    elif mood == "offline":
        paint = lambda t: [S(t, "mu b")]  # noqa: E731
    else:
        paint = lambda t: gradient(t, "b")  # noqa: E731
    rows = [top_border([S("Jarvis", "t2 b")], w), side([], w)]
    first, second = pick(what, room), pick(waiting, room)
    if room >= 16 and first is not None and second is not None:
        sep = [S("    "), S("│", "bd"), S("   ")]
        rows += [
            side([S("      ")] + paint(letters[0]) + [S("    "), S("│", "bd")], w),
            side([S("   "), glyph, S("  ")] + paint(letters[1]) + sep + [S(first, "t1")], w),
            side([S("      ")] + paint(letters[2]) + sep + [S(second, "mu")], w),
        ]
        span = max(0, min(bw, inner - 6) - 22)
        n = min(22, max(0, inner - 6))
    else:  # too narrow for block letters: the word in bold, the facts below it, whole
        rows.append(side([S("   "), glyph, S("  ")] + paint(word), w))
        for copy, cls in ((what, "t1"), (waiting, "mu")):
            text = pick(copy, inner - 7)
            lines = [text] if text is not None else textwrap.wrap(copy[-1], max(1, inner - 7))
            rows += [side([S("      "), S(line, cls)], w) for line in lines]
        span = max(0, inner - 6 - 12)
        n = min(12, max(0, inner - 6))
    rows.append(side([], w))
    pulse = None
    if mood == "busy" and n > 0:
        offset = 0
        if span:
            pos = frame % (2 * span)
            offset = pos if pos <= span else 2 * span - pos
        pulse = (7 + offset, n)
    rows.append(bottom_border(w, pulse=pulse))
    return rows


def voice_card(model: HostModel, w: int, frame: int, compact: bool) -> list[list[Seg]]:
    device = _first_device(model)
    title = [S("Voice device", "t2 b")]
    if device is not None:
        title.append(S(f"  {device.device_id}", "mu"))
    glyph, state, hint, moving = device_status(model)
    line = [S("   "), S(*glyph), S("  "), S(state, "t1 b")]
    if hint:
        line += [S("   ·   ", "mu"), S(hint, "t1")]
    inner = w - 2
    bottom = bottom_border(w, subtitle=connection(model))
    if compact:
        room = inner - width(line) - 4
        rows = [top_border(title, w)]
        if room >= 8:
            hi, lo = meter(room, frame, moving)
            rows.append(side(line + [S("   ")] + gradient(lo), w))
        else:
            rows.append(side(line, w))
        return rows + [bottom]
    hi, lo = meter(max(0, inner - 6), frame, moving)
    lo_segs = gradient(lo) if moving else [S(lo, "dm")]
    return [
        top_border(title, w),
        side([], w),
        side(line, w),
        side([S("   ")] + gradient(hi), w),
        side([S("   ")] + lo_segs, w),
        side([], w),
        bottom,
    ]


def header_rows(model: HostModel, live: Exchange | None, w: int, frame: int) -> list[list[Seg]]:
    if w < 6:
        return [region([], w)]
    if w >= 100:
        total = w - 2
        rw = max(36, min(49, round(total * 0.36)))
        lw = total - rw - 1
        left = jarvis_card(model, live, lw, frame)
        right = voice_card(model, rw, frame, compact=False)
        return [region([S(" ")] + a + [S(" ")] + b, w) for a, b in zip(left, right)]
    left = jarvis_card(model, live, w - 2, frame)
    right = voice_card(model, w - 2, frame, compact=True)
    return [region([S(" ")] + row, w) for row in left + right]


# --- Thread ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rail:
    x: int  # the rail line
    words: bool  # said / typed beside the time


def rail_for(w: int) -> Rail:
    return Rail(16, True) if w >= 90 else Rail(8, False)


def gutter(rail: Rail, kind: str = "line", exchange: Exchange | None = None) -> list[Seg]:
    if kind == "node" and exchange is not None:
        glyph, word = ("◎", "said") if exchange.source == "voice" else ("▸", "typed")
        time = clock(exchange.at) if exchange.at is not None else "     "
        cls = "lv b" if exchange.live else "t2"
        if rail.words:
            return [S("  "), S(time, "mu"), S("  "), S(word.rjust(5), "t2"), S("  "), S(glyph, cls), S(" ")]
        return [S("  "), S(time, "mu"), S(" "), S(glyph, cls), S(" ")]
    line = {"line": ("│", "bd"), "live": ("┃", "lv"), "more": ("╎", "dm")}[kind]
    return [S(" " * rail.x), S(*line), S(" ")]


def exchange_rows(e: Exchange, w: int, rail: Rail) -> list[list[Seg]]:
    c0 = rail.x + 2
    end = w - 1  # bubbles end one cell before the right edge
    lane = "live" if e.live else "line"
    rows: list[list[Seg]] = []

    # The user's words, right-aligned in a bubble.
    if e.said is not None or e.source == "voice":
        bubble_w = max(4, min(70, end - c0 - 4))
        if e.said:
            lines, cls = textwrap.wrap(e.said, bubble_w) or [""], "t1 bb"
        elif e.failure is not None:
            lines, cls = ["couldn't make it out"], "mu i bb"
        elif e.live:
            lines, cls = ["listening…"], "mu i bb"
        else:
            lines, cls = ["you spoke"], "mu i bb"
        bw = max(len(line) for line in lines)
        for j, line in enumerate(lines):
            g = gutter(rail, "node", e) if j == 0 else gutter(rail)
            bub = [S(" " + line.ljust(bw) + " ", cls)]
            pad = max(1, end - width(g) - width(bub))
            rows.append(region(g + [S(" " * pad)] + bub, w))
    else:
        rows.append(region(gutter(rail, "node", e), w))

    def jl(segs: list[Seg], kind: str = lane) -> None:
        rows.append(region(gutter(rail, kind) + segs, w))

    room = max(4, min(80, w - c0 - 4))
    if e.failure is not None and not e.answer:
        what, next_step, guidance = e.failure
        jl([S("✗", "er b"), S(" "), S(what, "er"), S(" " + next_step, "t1")])
        for line in textwrap.wrap(guidance, max(4, room)) or [""]:
            jl([S("  "), S(line, "t2")])
        return rows

    paragraphs = e.answer.split("\n") if e.answer else [""]
    lines = [line for p in paragraphs for line in (textwrap.wrap(p, room) or [""])]
    while len(lines) > 1 and lines[-1] == "":
        lines.pop()
    if e.live and not e.answer:  # no answer yet: what is happening, not an empty line
        jl([S("◆", "ac b"), S(" "), S(e.doing or "Thinking…", "lv")])
        lines = []
    for j, line in enumerate(lines):
        mark = S("◆" if j == 0 else " ", "ac b")
        segs = [mark, S(" "), S(line, "t1 b" if e.live else "t1")]
        if e.live and j == len(lines) - 1:
            segs.append(S("▍", "lv"))
        jl(segs)
    if e.failure is not None:  # failed after part of the answer
        what, next_step, guidance = e.failure
        jl([S("✗", "er b"), S(" "), S(what, "er"), S(" " + next_step, "t1")])
        jl([S("  "), S(clip(guidance, room), "t2")])
    for k, (state, text) in enumerate(e.did):
        label = S(ON_PC if k == 0 else " " * len(ON_PC), "mu")
        glyph = {"done": ("✓", "ok"), "live": ("◐", "lv"), "failed": ("✗", "er b")}.get(state, ("○", "mu"))
        text_cls = "lv" if state == "live" else "t2"
        tail = [S("    "), S("ctrl+a stops it", "mu")] if state == "live" and e.live else []
        avail = w - c0 - 2 - len(ON_PC) - 2 - width(tail)
        jl([S("  "), label, S(*glyph), S(" "), S(clip(text, max(4, avail)), text_cls)] + tail)
    if e.stopped:
        jl([S("  "), S("you stopped this answer", "mu")])
    return rows


def empty_hint(model: HostModel) -> str:
    """What to do to start, given what is connected."""

    talk = model.backend_status is ConnectionStatus.CONNECTED and any(d.online for d in model.devices.values())
    type_ = model.agent_status != OFFLINE
    if talk and type_:
        return "Talk to the voice device or type a message below."
    if talk:
        return "Talk to the voice device to start."
    if type_:
        return "Type a message below to start."
    return "Your conversation shows up here once Jarvis connects."


def thread_rows(model: HostModel, w: int, h: int) -> list[list[Seg]]:
    """``h`` rows: the divider, then the newest exchanges that fit, from the top down."""

    if h <= 0:
        return []
    rail = rail_for(w)
    items = exchanges(model)
    blocks = [exchange_rows(e, w, rail) for e in items]
    room = h - 3  # divider, rail row, blank row before the input
    shown: list[list[list[Seg]]] = []
    used = 0
    for block in reversed(blocks):
        need = len(block) + (1 if shown else 0)
        if used + need > room:
            break
        shown.insert(0, block)
        used += need
    hidden = len(blocks) - len(shown)
    if not shown and blocks and room > 0:  # the newest alone is taller than the room
        shown = [blocks[-1][-room:]]
        hidden = len(blocks) - 1
    chat: list[list[Seg]] = []
    for i, block in enumerate(shown):
        if i:
            chat.append(region(gutter(rail), w))
        chat += block
    title = [S("Today", "t2 b")] + ([S(f"  {hidden} earlier above", "mu")] if hidden else [])
    head = gutter(rail, "more") + title + [S(" ")]
    rule_n = w - 2 - width(head)
    divider = region(head + ([S("─" * rule_n, "bd")] if rule_n > 0 else []), w)
    if not items:  # a short hint, centred in the thread's region
        hint = [("Nothing here yet.", "t2 b")]
        hint += [(line, "mu") for line in textwrap.wrap(empty_hint(model), max(1, w - 4))]
        free = h - 1
        above = max(0, (free - len(hint)) // 2)
        rows = [blank(w, "bc")] * above
        for line, cls in hint:
            left = max(0, (w - len(line)) // 2)
            rows.append(region([S(" " * left), S(line, cls)], w))
        body = [divider] + rows
        return (body + [blank(w, "bc")] * (h - len(body)))[:h]
    body = [divider, region(gutter(rail), w)] + chat
    if len(body) > h:
        return body[-h:]
    return body + [blank(w, "bc")] * (h - len(body))


def render_rows(model: HostModel, w: int, h: int, frame: int = 0) -> list[list[Seg]]:
    """All rows of the user view for a ``w`` x ``h`` area (input and footer excluded)."""

    if w <= 0 or h <= 0:
        return []
    items = exchanges(model)
    live = next((e for e in reversed(items) if e.live), None)
    head = header_rows(model, live, w, frame)
    if len(head) + 4 > h:  # no room for the thread: the header alone, cut
        rows = head[:h]
        return rows + [blank(w, "bc")] * (h - len(rows))
    rows = head + [blank(w, "bc")]
    rows += thread_rows(model, w, h - len(rows))
    return [region(row, w) for row in rows[:h]]


class UserView(Widget):
    """The user view, redrawn from the model; a timer animates the live parts."""

    DEFAULT_CSS = """
    UserView {
        height: 1fr;
        background: $jarvis-background;
    }
    """

    def __init__(self, model: HostModel, *, animate: bool = True, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.model = model
        self.palette: Theme = THEMES[DEFAULT_DARK]
        self.animate = animate
        self.frame = 0
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        if self.animate:
            self._timer = self.set_interval(FRAME_S, self.tick, name="user-view-animation")

    def on_unmount(self) -> None:
        self.stop_animation()

    def stop_animation(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    @property
    def animating(self) -> bool:
        return self._timer is not None

    def tick(self) -> None:
        """One animation frame; only redraws when something on screen moves."""

        if is_animated(self.model):
            self.frame += 1
            self.refresh()

    def render(self) -> Text:
        rows = render_rows(self.model, self.size.width, self.size.height, self.frame)
        return to_text(rows, self.palette)
