"""Per-stage timing of one whole turn, as this host sees the daemon's frames (D17, S5).

A turn runs from the prompt leaving this host to the agent going back to ``idle``. In
between the agent loops: each model call ends in one assistant message (``message_done``;
an empty one when it only calls tools), each tool is an ``activity`` from ``running`` to a
final status, and a confirmation card waits for a person. The daemon's frames carry no
time and no "model call started" frame, so a model call is taken to start at the latest
boundary before its first frame: the echo, the previous ``message_done``, the end of a tool
or of a confirmation. Every figure is host-side, in seconds from sending.

``TurnLog`` appends one JSON line per turn. It never holds the prompt or a tool's title
(they may carry what the person typed), only lengths, kinds and times, and a failure to
write is remembered and logged once, never raised into the turn.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

RECORD_VERSION: Final = 1
LOG_ENV: Final = "JARVIS_TURN_LOG"
LOG_OFF: Final = frozenset({"", "off", "0", "-"})


@dataclass(slots=True)
class ModelCall:
    message_id: str
    start: float
    first_text: float | None = None
    end: float | None = None
    error: str | None = None


@dataclass(slots=True)
class ToolCall:
    id: str
    kind: str
    #: Index of the model call that asked for it (-1 when its message was not seen).
    call: int
    start: float
    end: float | None = None
    status: str = "running"


@dataclass(slots=True)
class Wait:
    key: str
    start: float
    end: float | None = None


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


@dataclass(slots=True)
class TurnTrace:
    """Monotonic stamps of one turn; ``record()`` makes them relative to ``sent_at``."""

    sent_at: float
    wall_at: float
    spoken: bool = False
    speech_end_at: float | None = None
    chars: int | None = None
    echo_at: float | None = None
    first_text_at: float | None = None
    end_at: float | None = None
    ended_by: str | None = None
    error: str | None = None
    stopped: bool = False
    calls: list[ModelCall] = field(default_factory=list)
    tools: list[ToolCall] = field(default_factory=list)
    waits: list[Wait] = field(default_factory=list)
    _boundary: float = 0.0

    def __post_init__(self) -> None:
        self._boundary = self.sent_at

    def _move(self, at: float) -> None:
        self._boundary = max(self._boundary, at)

    def _call(self, message_id: str) -> ModelCall:
        for call in reversed(self.calls):
            if call.message_id == message_id:
                return call
        call = ModelCall(message_id, self._boundary)
        self.calls.append(call)
        return call

    def echo(self, text: str, at: float) -> None:
        self.echo_at, self.chars = at, len(text)
        self._move(at)

    def delta(self, message_id: str, text: str, at: float) -> None:
        call = self._call(message_id)
        if text and call.first_text is None:
            call.first_text = at
        if text and self.first_text_at is None:
            self.first_text_at = at

    def done(self, message_id: str, text: str, at: float, error: str | None = None) -> None:
        call = self._call(message_id)
        call.end, call.error = at, error
        self.delta(message_id, text, at)  # the whole text may come at once
        self._move(at)

    def activity(self, activity_id: str, kind: str, status: str, message_id: str, at: float) -> None:
        tool = next((t for t in self.tools if t.id == activity_id), None)
        if tool is None:
            index = next((i for i, c in enumerate(self.calls) if c.message_id == message_id), -1)
            tool = ToolCall(activity_id, kind, index, at)
            self.tools.append(tool)
        tool.status = status
        if status != "running" and tool.end is None:
            tool.end = at
            self._move(at)

    def waits_open(self, keys: Iterable[str], at: float) -> None:
        """The confirmations up now: new keys start a wait, missing ones end theirs."""

        keys = set(keys)
        for wait in self.waits:
            if wait.end is None and wait.key not in keys:
                wait.end = at
                self._move(at)
        open_keys = {w.key for w in self.waits if w.end is None}
        for key in sorted(keys - open_keys):
            self.waits.append(Wait(key, at))

    def finish(self, at: float, reason: str) -> None:
        self.end_at, self.ended_by = at, reason
        for wait in self.waits:
            if wait.end is None:
                wait.end = at

    def _since(self, at: float | None) -> float | None:
        return None if at is None else at - self.sent_at

    @property
    def total_s(self) -> float | None:
        return self._since(self.end_at)

    @property
    def first_text_s(self) -> float | None:
        return self._since(self.first_text_at)

    @property
    def human_wait_s(self) -> float:
        return sum(w.end - w.start for w in self.waits if w.end is not None)

    @property
    def model_s(self) -> float:
        return sum(c.end - c.start for c in self.calls if c.end is not None)

    @property
    def tool_s(self) -> float:
        return sum(t.end - t.start for t in self.tools if t.end is not None)

    def summary(self) -> str:
        """``2 calls 3.8s · 1 tool · wait 3.0s``: model calls, tools, a person's answers (fits the card)."""

        n, t = len(self.calls), len(self.tools)
        parts = [f"{n} call{'s' * (n != 1)} {self.model_s:.1f}s", f"{t} tool{'s' * (t != 1)}"]
        if self.waits:
            parts.append(f"wait {self.human_wait_s:.1f}s")
        return " · ".join(parts)

    def record(self) -> dict[str, Any]:
        """The JSON-ready record: seconds from sending, rounded to the millisecond."""

        since = self._since
        return {
            "v": RECORD_VERSION,
            "at": datetime.fromtimestamp(self.wall_at, UTC).isoformat(timespec="milliseconds"),
            "spoken": self.spoken,
            "chars": self.chars,
            "stt_s": _r(None if self.speech_end_at is None else self.sent_at - self.speech_end_at),
            "echo_s": _r(since(self.echo_at)),
            "first_text_s": _r(self.first_text_s),
            "total_s": _r(self.total_s),
            "ended_by": self.ended_by,
            "error": self.error,
            "stopped": self.stopped,
            "model_calls_n": len(self.calls),
            "tools_n": len(self.tools),
            "model_s": _r(self.model_s),
            "tool_s": _r(self.tool_s),
            "human_wait_s": _r(self.human_wait_s),
            "model_calls": [
                {"start_s": _r(since(c.start)), "end_s": _r(since(c.end)),
                 "first_text_s": _r(since(c.first_text)), "error": c.error}
                for c in self.calls
            ],
            "tools": [
                {"kind": t.kind, "status": t.status, "start_s": _r(since(t.start)),
                 "end_s": _r(since(t.end)), "call": t.call}
                for t in self.tools
            ],
            "waits": [{"start_s": _r(since(w.start)), "end_s": _r(since(w.end))} for w in self.waits],
        }


def default_log_path(env: Mapping[str, str] | None = None) -> Path | None:
    """``$JARVIS_TURN_LOG`` (``off`` disables), else ``$XDG_STATE_HOME/jarvis/turns.jsonl``."""

    env = os.environ if env is None else env
    if LOG_ENV in env:
        value = env[LOG_ENV].strip()
        return None if value.lower() in LOG_OFF else Path(value).expanduser()
    state = env.get("XDG_STATE_HOME")
    if state:
        return Path(state) / "jarvis" / "turns.jsonl"
    home = Path(env["HOME"]) if env.get("HOME") else Path.home()
    return home / ".local" / "state" / "jarvis" / "turns.jsonl"


class TurnLog:
    """Appends ``trace.record()`` as one JSON line; never raises into the turn."""

    def __init__(self, path: Path, extra: Mapping[str, Any] | None = None) -> None:
        self.path = path
        self.extra = dict(extra or {})
        self.failed = False

    def write(self, trace: TurnTrace) -> None:
        try:
            line = json.dumps({**trace.record(), **self.extra}, ensure_ascii=False) + "\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception as error:  # noqa: BLE001 - timing must never break a turn
            if not self.failed:
                logger.warning("turn log %s not written: %s", self.path, error)
            self.failed = True
