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

    async def __call__(self) -> FakePi:
        process = FakePi(self)
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


def die(*chunks: str, code: int = 1) -> Behavior:
    """Stream ``chunks`` and then exit, as a crash would."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        _start(pi, record)
        for chunk in chunks:
            _delta(pi, chunk)
        await asyncio.sleep(0)
        pi.exit(code)

    return behavior
