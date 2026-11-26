"""A scripted stand-in for ``pi --mode rpc``: JSON records in, JSON records out, no model.

Each ``prompt`` written to a ``FakePi`` runs the next behavior of its ``FakeLauncher``
(the queue is shared by every process the launcher starts, so a restart continues the
script). Behaviors emit the records Pi documents (``docs/rpc.md``, ``docs/json.md``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

Behavior = Callable[["FakePi", dict[str, Any]], Awaitable[None]]

#: What ``get_available_models`` lists (Pi's Model objects, trimmed to the fields used).
MODELS: list[dict[str, Any]] = [
    {"id": "test-model", "name": "Test Model", "provider": "openai", "reasoning": True,
     "input": ["text", "image"]},
    {"id": "plain", "name": "Plain", "provider": "openai", "reasoning": False, "input": ["text"]},
    {"id": "claude-x", "name": "Claude X", "provider": "anthropic", "reasoning": True,
     "input": ["text"]},
]
REASONING_LEVELS = ["off", "minimal", "low", "medium", "high"]


class FakePi:
    def __init__(self, launcher: FakeLauncher) -> None:
        self._launcher = launcher
        self._out: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.written: list[dict[str, Any]] = []
        self.exit_code: int | None = None
        self.closed = False
        self.aborted = asyncio.Event()
        self.running = False
        self._tasks: set[asyncio.Task[None]] = set()
        self.model: dict[str, Any] = MODELS[0]
        self.thinking = "low"
        #: Answers to ``extension_ui_request``s, by request id.
        self.ui_answers: dict[str, asyncio.Future[dict[str, Any]]] = {}
        #: Commands never answered (to test a Pi that stops talking).
        self.mute: set[str] = set()
        #: Commands answered with ``success: false`` (``set_thinking_level`` when the level is listed).
        self.refuse: set[str] = set()
        #: Thinking levels ``set_thinking_level`` refuses.
        self.refused_levels: set[str] = set()
        self.last_answer: dict[str, Any] | None = None
        #: Sees each command first (it may wait); ``True`` when it answered the command itself.
        self.intercept: Callable[[FakePi, dict[str, Any]], Awaitable[bool]] | None = None

    # PiProcess

    async def write(self, record: Mapping[str, Any]) -> None:
        if self.exit_code is not None:
            raise BrokenPipeError("pi exited")
        record = dict(record)
        self.written.append(record)
        kind = record.get("type")
        if kind == "prompt":
            behavior = self._launcher.next_behavior()
            task = asyncio.ensure_future(self._run(behavior, record))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif kind == "abort":
            self.aborted.set()
            if not self.running:
                self.respond(record, "abort")
        elif kind == "extension_ui_response":
            future = self.ui_answers.setdefault(record["id"], asyncio.get_running_loop().create_future())
            if not future.done():
                future.set_result(record)
        elif isinstance(kind, str) and kind not in self.mute:
            if self.intercept is None or not await self.intercept(self, record):
                self._command(record, kind)

    def _command(self, record: dict[str, Any], kind: str) -> None:
        refused = kind in self.refuse or (kind == "set_thinking_level" and record.get("level") in self.refused_levels)
        if refused:
            self.emit({"id": record.get("id"), "type": "response", "command": kind, "success": False,
                       "error": f"{kind} refused"})
            return
        if kind == "get_available_models":
            self.respond(record, kind, data={"models": MODELS})
        elif kind == "get_state":
            self.respond(record, kind, data={"model": self.model, "thinkingLevel": self.thinking,
                                             "isStreaming": self.running})
        elif kind == "get_available_thinking_levels":
            levels = REASONING_LEVELS if self.model.get("reasoning") else ["off"]
            self.respond(record, kind, data={"levels": levels})
        elif kind == "set_model":
            found = next((m for m in MODELS if (m["provider"], m["id"]) == (record.get("provider"),
                                                                          record.get("modelId"))), None)
            if found is None:
                self.emit({"id": record.get("id"), "type": "response", "command": kind, "success": False,
                           "error": "Model not found"})
                return
            self.model = found
            self.thinking = self.thinking if found.get("reasoning") else "off"
            self.respond(record, kind, data=found)
        elif kind == "set_thinking_level":
            self.thinking = record["level"]
            self.respond(record, kind)
        else:
            self.emit({"id": record.get("id"), "type": "response", "command": kind, "success": False,
                       "error": f"Unknown command: {kind}"})

    async def ask(self, method: str, **fields: Any) -> dict[str, Any]:
        """Emit an ``extension_ui_request`` and wait for its ``extension_ui_response``."""

        request_id = f"ui-{len(self.ui_answers) + 1}"
        future = self.ui_answers.setdefault(request_id, asyncio.get_running_loop().create_future())
        self.emit({"type": "extension_ui_request", "id": request_id, "method": method, **fields})
        return await future

    async def read(self) -> dict[str, Any] | None:
        return await self._out.get()

    async def close(self) -> None:
        self.closed = True
        if self.exit_code is None:
            self.exit(0)

    # Script helpers

    async def _run(self, behavior: Behavior, record: dict[str, Any]) -> None:
        self.running = True
        try:
            await behavior(self, record)
        finally:
            self.running = False

    def emit(self, record: dict[str, Any]) -> None:
        if self.exit_code is None:
            self._out.put_nowait(record)

    def exit(self, code: int) -> None:
        if self.exit_code is None:
            self.exit_code = code
            self._out.put_nowait(None)

    def respond(self, record: Mapping[str, Any], command: str, **fields: Any) -> None:
        response: dict[str, Any] = {"type": "response", "command": command, "success": True, **fields}
        if "id" in record:
            response["id"] = record["id"]
        self.emit(response)

    def abort_record(self) -> dict[str, Any]:
        return next(r for r in reversed(self.written) if r.get("type") == "abort")


class FakeLauncher:
    def __init__(self, *behaviors: Behavior) -> None:
        self._behaviors = list(behaviors)
        self.launches: list[FakePi] = []
        #: Called with each new process before the bridge gets it (to script a restarted Pi).
        self.on_launch: Callable[[FakePi], Awaitable[None]] | None = None

    async def __call__(self) -> FakePi:
        process = FakePi(self)
        if self.on_launch is not None:
            await self.on_launch(process)
        self.launches.append(process)
        return process

    def next_behavior(self) -> Behavior:
        assert self._behaviors, "the fake Pi got a prompt nobody scripted"
        return self._behaviors.pop(0)

    @property
    def current(self) -> FakePi:
        return self.launches[-1]


# --- Behaviors ------------------------------------------------------------------------


def _start(pi: FakePi, record: dict[str, Any]) -> None:
    pi.respond(record, "prompt", data={"disposition": "started"})
    user = {"role": "user", "content": record["message"], "timestamp": 1}
    pi.emit({"type": "agent_start"})
    pi.emit({"type": "turn_start"})
    pi.emit({"type": "message_start", "message": user})
    pi.emit({"type": "message_end", "message": user})
    pi.emit({"type": "message_start", "message": {"role": "assistant", "content": [], "stopReason": "pending"}})


def _delta(pi: FakePi, text: str) -> None:
    pi.emit(
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": text},
        }
    )


def _assistant(text: str, stop: str, **extra: Any) -> dict[str, Any]:
    content = [{"type": "text", "text": text}] if text else []
    return {"role": "assistant", "content": content, "stopReason": stop, **extra}


def _end(pi: FakePi, message: dict[str, Any], *, aborted: bool = False) -> None:
    pi.emit({"type": "message_end", "message": message})
    pi.emit({"type": "turn_end", "message": message, "toolResults": []})
    pi.emit({"type": "agent_end", "messages": [message], "willRetry": False})
    pi.emit({"type": "agent_settled", "aborted": aborted})


def reply(*chunks: str, gate: asyncio.Event | None = None) -> Behavior:
    """Stream ``chunks`` as text deltas; with ``gate``, wait for it before finishing."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        for chunk in chunks:
            await asyncio.sleep(0)
            _delta(pi, chunk)
        if gate is not None:
            await gate.wait()
        _end(pi, _assistant("".join(chunks), "stop"))

    return behavior


def hang(*chunks: str) -> Behavior:
    """Stream ``chunks``, then wait for an ``abort`` and end the run as aborted."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        for chunk in chunks:
            _delta(pi, chunk)
        await pi.aborted.wait()
        _end(pi, _assistant("".join(chunks), "aborted", errorMessage="Request was aborted"), aborted=True)
        pi.respond(pi.abort_record(), "abort")

    return behavior


def fail(message: str, *chunks: str) -> Behavior:
    """The provider fails after ``chunks``: the assistant message ends with ``error``."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        for chunk in chunks:
            _delta(pi, chunk)
        _end(pi, _assistant("".join(chunks), "error", errorMessage=message))

    return behavior


def reject(message: str) -> Behavior:
    """Pi refuses the prompt command itself (``success: false``)."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        pi.emit({"id": record["id"], "type": "response", "command": "prompt", "success": False, "error": message})

    return behavior


def tool_turn(
    tool: str = "pc_media",
    args: dict[str, Any] | None = None,
    *,
    final: str = "Listo.",
    is_error: bool = False,
    call_id: str = "call_1",
) -> Behavior:
    """An assistant message that only calls ``tool``, the tool's run, then a final answer."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        arguments = args or {"action": "volume_up"}
        call = {"type": "toolCall", "id": call_id, "name": tool, "arguments": arguments}
        pi.emit({"type": "message_update", "assistantMessageEvent": {"type": "toolcall_start", "contentIndex": 0}})
        calling = {"role": "assistant", "content": [call], "stopReason": "toolUse"}
        pi.emit({"type": "message_end", "message": calling})
        pi.emit({"type": "tool_execution_start", "toolCallId": call_id, "toolName": tool, "args": arguments})
        await asyncio.sleep(0)
        result = {"content": [{"type": "text", "text": "failed" if is_error else "ok"}], "details": {}}
        pi.emit({"type": "tool_execution_update", "toolCallId": call_id, "toolName": tool, "args": arguments,
                 "partialResult": result})
        pi.emit({"type": "tool_execution_end", "toolCallId": call_id, "toolName": tool, "result": result,
                 "isError": is_error, "durationMs": 3})
        tool_result = {"role": "toolResult", "toolCallId": call_id, "toolName": tool,
                       "content": result["content"], "isError": is_error}
        pi.emit({"type": "message_start", "message": tool_result})
        pi.emit({"type": "message_end", "message": tool_result})
        pi.emit({"type": "turn_end", "message": calling, "toolResults": [tool_result]})
        pi.emit({"type": "turn_start"})
        pi.emit({"type": "message_start", "message": {"role": "assistant", "content": [], "stopReason": "pending"}})
        _delta(pi, final)
        _end(pi, _assistant(final, "stop"))

    return behavior


def asks(method: str, *, final: str = "ok", **fields: Any) -> Behavior:
    """Ask the user through the extension UI, then reply with the answer it got."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        answer = await pi.ask(method, **fields)
        pi.last_answer = answer
        _delta(pi, final)
        _end(pi, _assistant(final, "stop"))

    return behavior


def silent(*chunks: str) -> Behavior:
    """Accept the prompt, stream ``chunks`` and then never write again (a stuck Pi)."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        for chunk in chunks:
            _delta(pi, chunk)
        await asyncio.Event().wait()

    return behavior


def die(*chunks: str, code: int = 1) -> Behavior:
    """Stream ``chunks`` and then exit, as a crash would."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        for chunk in chunks:
            _delta(pi, chunk)
        await asyncio.sleep(0)
        pi.exit(code)

    return behavior
