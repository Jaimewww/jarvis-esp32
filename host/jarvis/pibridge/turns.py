"""One Pi prompt per ``send``, as the frames Gentle Dot's clients expect (S4, S5).

A turn emits ``agent_state`` working, the ``user_message`` echo once Pi accepts the
prompt, a ``message_delta`` per Pi text delta and a ``message_done`` (whole text) per
assistant message, and ``agent_state`` idle when Pi settles. A Pi error ends the
message with ``error`` and adds an ``error`` frame; a prompt Pi refuses is an ``error``
frame with no echo. ``abort`` asks Pi to stop; the cut message ends ``stopped``.

Each Pi tool run is an ``activity`` (S6): ``running`` on ``tool_execution_start``, then
``done`` or ``failed`` on ``tool_execution_end``, under the assistant message that called
it. That message ends first, empty when it only calls tools, as Gentle Dot does, so the
clients count one model call per assistant message and one tool per activity. A tool still
running when the turn ends is reported ``failed``.

One turn at a time (D14): a ``send`` during a turn waits in a queue (reported with the
``queue`` frame) and runs when the turn ends; ``abort`` drops it too. If Pi exits, the
turn ends with an ``error`` frame and the next ``send`` starts Pi again.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .pi import PiLauncher, PiProcess

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]

#: Pi events that belong to a run: the echo goes out before any of them is relayed.
_RUN_EVENTS = frozenset({
    "agent_start", "turn_start", "message_start", "message_update", "message_end",
    "tool_execution_start", "tool_execution_end",
})


@dataclass(slots=True)
class _Tool:
    message_id: str
    name: str


@dataclass(slots=True)
class _Turn:
    text: str
    user_id: str
    request_id: str
    echoed: bool = False
    abort_requested: bool = False
    replied: bool = False  # some message_done went out
    open_id: str | None = None
    open_text: list[str] = field(default_factory=list)
    last_id: str = ""  # the last assistant message: the one whose tool calls run now
    tools: dict[str, _Tool] = field(default_factory=dict)


def _text_of(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    )


class Bridge:
    def __init__(self, launcher: PiLauncher, emit: Emit, *, model: str) -> None:
        self._launcher = launcher
        self._emit = emit
        self._model = model
        self._ids = itertools.count(1)
        self._process: PiProcess | None = None
        self._reader: asyncio.Task[None] | None = None
        self._turn: _Turn | None = None
        self._queue: deque[str] = deque()
        self._state = "idle"

    @property
    def agent_state(self) -> str:
        return self._state

    @property
    def model(self) -> str:
        return self._model

    async def start(self) -> None:
        """Start Pi now rather than on the first ``send``; a failure is only logged."""

        try:
            await self._ensure_process()
        except Exception:  # noqa: BLE001 - the first send reports it to the clients
            logger.exception("could not start pi")

    async def send(self, text: str) -> None:
        if self._turn is not None:
            self._queue.append(text)
            await self._emit_queue()
            return
        await self._begin(text)

    async def abort(self) -> None:
        turn = self._turn
        if turn is None:
            return  # as the daemon: an abort with nothing running gets no answer
        turn.abort_requested = True
        if self._queue:
            self._queue.clear()
            await self._emit_queue()
        process = self._process
        if process is None:
            return
        with contextlib.suppress(OSError):
            await process.write({"id": f"abort-{next(self._ids)}", "type": "abort"})

    async def close(self) -> None:
        process, reader = self._process, self._reader
        self._process = self._reader = None
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        if process is not None:
            await process.close()

    # Turns

    async def _begin(self, text: str) -> None:
        n = next(self._ids)
        turn = _Turn(text, user_id=f"u{n}", request_id=f"prompt-{n}")
        self._turn = turn
        await self._set_state("working")
        try:
            process = await self._ensure_process()
        except Exception as error:  # noqa: BLE001 - any launch failure ends the turn
            logger.warning("could not start pi: %s", error)
            await self._emit({"type": "error", "code": "agent_unavailable", "message": f"Pi did not start: {error}"})
            await self._finish(turn)
            return
        try:
            await process.write({"id": turn.request_id, "type": "prompt", "message": text})
        except OSError:
            pass  # Pi is gone; its reader ends the turn

    async def _finish(self, turn: _Turn) -> None:
        if self._turn is not turn:
            return
        if turn.open_id is not None:
            await self._done(turn, "".join(turn.open_text), stopped=turn.abort_requested)
        elif turn.abort_requested and turn.echoed and not turn.replied:
            # Stopped before Pi wrote anything: still end the reply the clients wait for.
            await self._emit({"type": "message_done", "messageId": f"a{next(self._ids)}", "text": "", "stopped": True})
        for call_id in list(turn.tools):
            await self._tool_end(turn, call_id, None, failed=True)
        self._turn = None
        if self._queue:
            text = self._queue.popleft()
            await self._emit_queue()
            await self._begin(text)
            return
        await self._set_state("idle")

    async def _echo(self, turn: _Turn) -> None:
        if not turn.echoed:
            turn.echoed = True
            await self._emit({"type": "user_message", "messageId": turn.user_id, "text": turn.text})

    async def _done(self, turn: _Turn, text: str, *, stopped: bool = False, error: str | None = None) -> None:
        frame: dict[str, Any] = {"type": "message_done", "messageId": turn.open_id, "text": text}
        if stopped:
            frame["stopped"] = True
        if error:
            frame["error"] = error
        turn.last_id = turn.open_id or turn.last_id
        turn.open_id = None
        turn.open_text = []
        turn.replied = True
        await self._emit(frame)

    async def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            await self._emit({"type": "agent_state", "state": state})

    async def _emit_queue(self) -> None:
        await self._emit({"type": "queue", "steering": [], "followUp": list(self._queue)})

    # Pi

    async def _ensure_process(self) -> PiProcess:
        process = self._process
        if process is not None and process.exit_code is None:
            return process
        process = await self._launcher()
        self._process = process
        self._reader = asyncio.ensure_future(self._read(process))
        return process

    async def _read(self, process: PiProcess) -> None:
        while True:
            record = await process.read()
            if record is None:
                break
            try:
                await self._on_record(record)
            except Exception:  # noqa: BLE001 - one bad record must not stop the reader
                logger.exception("failed to relay a pi record")
        if self._process is process:
            self._process = None
            self._reader = None
            await self._on_exit(process)

    async def _on_exit(self, process: PiProcess) -> None:
        message = f"Pi exited (code {process.exit_code})."
        logger.warning("%s", message)
        turn = self._turn
        if turn is None:
            return
        if turn.open_id is not None:
            await self._done(turn, "".join(turn.open_text), error=message)
        await self._emit({"type": "error", "code": "agent_exited", "message": message})
        await self._finish(turn)

    async def _on_record(self, record: dict[str, Any]) -> None:
        turn = self._turn
        if turn is None:
            return
        kind = record.get("type")
        if kind == "response":
            if record.get("id") == turn.request_id:
                await self._on_prompt_response(turn, record)
            return
        if kind in _RUN_EVENTS:
            await self._echo(turn)
        if kind == "message_start":
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                self._open(turn)
        elif kind == "message_update":
            event = record.get("assistantMessageEvent")
            if isinstance(event, dict) and event.get("type") == "text_delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    message_id = self._open(turn)
                    turn.open_text.append(delta)
                    await self._emit({"type": "message_delta", "messageId": message_id, "delta": delta})
        elif kind == "message_end":
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                await self._on_assistant_end(turn, message)
        elif kind == "tool_execution_start":
            await self._tool_start(turn, record)
        elif kind == "tool_execution_end":
            call_id = record.get("toolCallId")
            if isinstance(call_id, str):
                await self._tool_end(turn, call_id, record.get("toolName"), failed=record.get("isError") is True)
        elif kind == "agent_settled":
            await self._finish(turn)

    async def _tool_start(self, turn: _Turn, record: dict[str, Any]) -> None:
        call_id, name = record.get("toolCallId"), record.get("toolName")
        if not isinstance(call_id, str) or not call_id:
            return
        tool = _Tool(turn.open_id or turn.last_id, name if isinstance(name, str) and name else "tool")
        turn.tools[call_id] = tool
        await self._activity(call_id, tool, "running")

    async def _tool_end(self, turn: _Turn, call_id: str, name: object, *, failed: bool) -> None:
        tool = turn.tools.pop(call_id, None)
        if tool is None:  # its start was not seen: still report the outcome
            tool = _Tool(turn.last_id, name if isinstance(name, str) and name else "tool")
        await self._activity(call_id, tool, "failed" if failed else "done")

    async def _activity(self, call_id: str, tool: _Tool, status: str) -> None:
        activity = {"id": call_id, "kind": tool.name, "title": tool.name, "status": status}
        await self._emit({"type": "activity", "messageId": tool.message_id, "activity": activity})

    def _open(self, turn: _Turn) -> str:
        if turn.open_id is None:
            turn.open_id = f"a{next(self._ids)}"
            turn.open_text = []
        return turn.open_id

    async def _on_prompt_response(self, turn: _Turn, record: dict[str, Any]) -> None:
        if record.get("success") is not True:
            error = record.get("error")
            message = error if isinstance(error, str) and error else "Pi refused the prompt."
            await self._emit({"type": "error", "code": "prompt_rejected", "message": message})
            await self._finish(turn)
            return
        await self._echo(turn)
        data = record.get("data")
        if isinstance(data, dict) and data.get("disposition") == "handled":
            await self._finish(turn)  # an extension consumed it; no run follows

    async def _on_assistant_end(self, turn: _Turn, message: dict[str, Any]) -> None:
        self._open(turn)
        final = _text_of(message)
        text = final if final is not None else "".join(turn.open_text)
        stop = message.get("stopReason")
        if stop == "aborted" or (turn.abort_requested and stop == "error"):
            await self._done(turn, text, stopped=True)
            return
        if stop == "error":
            raw = message.get("errorMessage")
            error = raw if isinstance(raw, str) and raw else "Pi reported an error."
            await self._done(turn, text, error=error)
            await self._emit({"type": "error", "code": "agent_error", "message": error})
            return
        await self._done(turn, text)
