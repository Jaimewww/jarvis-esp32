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

Besides prompts, the bridge sends Pi commands and waits for their ``response`` by ``id``
(models, S8), and relays the extension's questions (S7, ``questions``): a dialog is an
``ask`` to every client, the first ``ui_response`` answers it and ``ask_resolved`` goes to
every client; with no client connected, the dialog is denied at once.

One turn at a time (D14): a ``send`` during a turn waits in a queue (reported with the
``queue`` frame) and runs when the turn ends; ``abort`` drops it too. The queue holds
``queue_cap`` messages; ``send`` refuses one more. If Pi exits, the turn ends with an
``error`` frame and the next ``send`` starts Pi again. A turn with no Pi record for
``record_timeout`` seconds (a question waiting for the user does not count) is stuck: it
ends with an ``error`` frame and Pi is stopped, to start again on the next ``send`` (S12.4).
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

from . import models as model_frames
from . import questions
from .models import ModelError
from .pi import PiLauncher, PiProcess

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]

#: How long a Pi command (not a prompt) may take to answer.
COMMAND_TIMEOUT_S = 10.0
#: A turn with no Pi record for this long is stuck (S12.4); a pending question pauses it.
RECORD_TIMEOUT_S = 120.0
#: ``send``s that may wait behind the running turn.
QUEUE_CAP = 16

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
    def __init__(
        self,
        launcher: PiLauncher,
        emit: Emit,
        *,
        model: str,
        record_timeout: float = RECORD_TIMEOUT_S,
        queue_cap: int = QUEUE_CAP,
    ) -> None:
        self._record_timeout = record_timeout
        self._queue_cap = queue_cap
        self._launcher = launcher
        self._emit = emit
        self._model = model
        self._ids = itertools.count(1)
        self._process: PiProcess | None = None
        self._reader: asyncio.Task[None] | None = None
        self._turn: _Turn | None = None
        self._queue: deque[str] = deque()
        self._state = "idle"
        self._commands: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._questions = questions.Questions()
        self._clients = 0
        self._launching = asyncio.Lock()
        self._last_record = 0.0
        self._watchdog: asyncio.Task[None] | None = None

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

    async def send(self, text: str) -> bool:
        """Run ``text`` now or after the running turn; ``False`` when the queue is full."""

        if self._turn is not None:
            if len(self._queue) >= self._queue_cap:
                return False
            self._queue.append(text)
            await self._emit_queue()
            return True
        await self._begin(text)
        return True

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

    # Questions (S7)

    async def clients_changed(self, count: int) -> None:
        """The number of connected clients; with none left, waiting questions are denied."""

        self._clients = count
        if count == 0:
            for request_id in self._questions.drain():
                await self._reply_ui(questions.denial(request_id))

    async def answer(self, request_id: str, response: dict[str, Any]) -> None:
        """A client's ``ui_response``; one to a question no longer waiting is ignored."""

        method = self._questions.close(request_id)
        if method is not None:
            await self._reply_ui(questions.answer(request_id, method, response))

    async def _reply_ui(self, record: dict[str, Any]) -> None:
        process = self._process
        if process is not None:
            with contextlib.suppress(OSError):
                await process.write(record)
        await self._emit({"type": "ask_resolved", "requestId": record["id"]})

    async def _on_ui_request(self, record: dict[str, Any]) -> None:
        request_id, method = record.get("id"), record.get("method")
        if not isinstance(request_id, str) or method not in questions.DIALOGS:
            return  # fire and forget: nothing waits for an answer
        if self._clients == 0:
            process = self._process
            if process is not None:
                with contextlib.suppress(OSError):
                    await process.write(questions.denial(request_id))
            return
        self._questions.open(request_id, str(method))
        await self._emit(questions.ask_frame(record))

    async def _resolve_questions(self) -> None:
        for request_id in self._questions.drain():
            await self._emit({"type": "ask_resolved", "requestId": request_id})

    # Models (S8)

    async def models(self) -> dict[str, Any]:
        """The ``models`` frame: Pi's models, the running one and its thinking level."""

        listed = await self._data({"type": "get_available_models"})
        state = await self._data({"type": "get_state"})
        levels = await self._data({"type": "get_available_thinking_levels"})
        available = [m for m in listed.get("models") or [] if isinstance(m, dict)]
        current = state.get("model")
        raw_levels = levels.get("levels")
        return model_frames.models_frame(
            available,
            current if isinstance(current, dict) else None,
            state.get("thinkingLevel"),
            [level for level in raw_levels if isinstance(level, str)] if isinstance(raw_levels, list) else [],
        )

    async def set_model(self, provider: str, model_id: str, thinking: str | None) -> dict[str, Any]:
        """Switch Pi's model (and thinking level); the new ``models`` frame, or ``ModelError``."""

        listed = await self._data({"type": "get_available_models"})
        available = [m for m in listed.get("models") or [] if isinstance(m, dict)]
        target = model_frames.find(available, provider, model_id)
        if target is None:
            raise ModelError("model_unavailable", f"Pi has no model {provider}/{model_id}.")
        if thinking is not None and thinking not in model_frames.THINKING_LEVELS:
            raise ModelError("thinking_unavailable", f"Unknown thinking level {thinking[:20]}.")
        if thinking not in (None, "off") and not model_frames.reasons(target):
            raise ModelError("thinking_unavailable", "That model does not support that thinking level.")
        switched = await self._command({"type": "set_model", "provider": provider, "modelId": model_id})
        if switched.get("success") is not True:
            raise ModelError("model_switch_failed", _error_of(switched, "Pi could not switch the model."))
        self._model = f"{provider}/{model_id}"
        if thinking is not None:
            levels = (await self._data({"type": "get_available_thinking_levels"})).get("levels")
            if not isinstance(levels, list) or thinking not in levels:
                raise ModelError("thinking_unavailable", "That model does not support that thinking level.")
            done = await self._command({"type": "set_thinking_level", "level": thinking})
            if done.get("success") is not True:
                raise ModelError("thinking_unavailable", _error_of(done, "Pi refused that thinking level."))
        return await self.models()

    async def _data(self, record: dict[str, Any]) -> dict[str, Any]:
        response = await self._command(record)
        if response.get("success") is not True:
            raise ModelError("agent_unavailable", _error_of(response, f"Pi refused {record['type']}."))
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    async def _command(self, record: dict[str, Any]) -> dict[str, Any]:
        """Send one Pi command and wait for its ``response``."""

        try:
            process = await self._ensure_process()
        except Exception as error:  # noqa: BLE001 - any launch failure is "unavailable"
            raise ModelError("agent_unavailable", f"Pi did not start: {error}") from error
        command_id = f"cmd-{next(self._ids)}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._commands[command_id] = future
        try:
            await process.write({**record, "id": command_id})
            return await asyncio.wait_for(future, COMMAND_TIMEOUT_S)
        except (OSError, ConnectionError, TimeoutError) as error:
            raise ModelError("agent_unavailable", "Pi did not answer.") from error
        finally:
            self._commands.pop(command_id, None)

    async def close(self) -> None:
        self._stop_watchdog()
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
        self._last_record = asyncio.get_running_loop().time()
        self._watchdog = asyncio.ensure_future(self._watch(turn))
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
        self._stop_watchdog()
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

    # A stuck turn (S12.4)

    def _stop_watchdog(self) -> None:
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()

    async def _watch(self, turn: _Turn) -> None:
        loop = asyncio.get_running_loop()
        while self._turn is turn:
            if self._questions:
                self._last_record = loop.time()  # the person is answering: not stuck
            quiet = loop.time() - self._last_record
            if quiet >= self._record_timeout:
                await self._time_out(turn)
                return
            await asyncio.sleep(min(self._record_timeout - quiet, 0.5))

    async def _time_out(self, turn: _Turn) -> None:
        message = f"Pi wrote nothing for {self._record_timeout:g}s; the reply was stopped and Pi restarts."
        logger.warning("%s", message)
        process, reader = self._process, self._reader
        self._process = self._reader = None  # the next send starts a new Pi
        self._fail_commands()
        await self._resolve_questions()
        if turn.open_id is not None:
            await self._done(turn, "".join(turn.open_text), error=message)
        await self._emit({"type": "error", "code": "agent_timeout", "message": message})
        await self._finish(turn)
        if reader is not None:
            reader.cancel()
        if process is not None:
            await process.close()

    # Pi

    async def _ensure_process(self) -> PiProcess:
        async with self._launching:
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
            self._last_record = asyncio.get_running_loop().time()
            try:
                await self._on_record(record)
            except Exception:  # noqa: BLE001 - one bad record must not stop the reader
                logger.exception("failed to relay a pi record")
        if self._process is process:
            self._process = None
            self._reader = None
            await self._on_exit(process)

    def _fail_commands(self) -> None:
        for future in self._commands.values():
            if not future.done():
                future.set_exception(ConnectionError("pi exited"))

    async def _on_exit(self, process: PiProcess) -> None:
        message = f"Pi exited (code {process.exit_code})."
        logger.warning("%s", message)
        self._fail_commands()
        await self._resolve_questions()
        turn = self._turn
        if turn is None:
            return
        if turn.open_id is not None:
            await self._done(turn, "".join(turn.open_text), error=message)
        await self._emit({"type": "error", "code": "agent_exited", "message": message})
        await self._finish(turn)

    async def _on_record(self, record: dict[str, Any]) -> None:
        kind = record.get("type")
        if kind == "response":
            future = self._commands.get(str(record.get("id")))
            if future is not None:
                if not future.done():
                    future.set_result(record)
                return
        elif kind == "extension_ui_request":
            await self._on_ui_request(record)
            return
        turn = self._turn
        if turn is None:
            return
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


def _error_of(response: dict[str, Any], fallback: str) -> str:
    error = response.get("error")
    return error if isinstance(error, str) and error else fallback
