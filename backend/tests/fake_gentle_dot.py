"""A fake Gentle Dot daemon (protocol v1) on localhost, for the adapter tests.

It speaks the protocol as ``host/jarvis/client.py`` documents it: the first client
frame is ``{"type": "hello", "token", "protocol": 1}``; a good hello gets ``ready``,
a wrong token gets close code 4401 and a wrong protocol 4400. Every server frame
carries a monotonic ``seq``. What happens after a ``send`` or an ``abort`` is a
per-test script, and every frame the client sent is recorded.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

TOKEN = "test-token"

Script = Callable[["Session", str], Awaitable[None]]
AbortScript = Callable[["Session"], Awaitable[None]]


class Session:
    """One client connection as the fake daemon sees it."""

    def __init__(self, ws: ServerConnection) -> None:
        self.ws = ws
        self.seq = 0
        self.received: list[dict[str, Any]] = []
        self.closed = asyncio.Event()

    async def emit(self, payload: dict[str, Any]) -> None:
        self.seq += 1
        await self.ws.send(json.dumps({**payload, "seq": self.seq}))

    async def raw(self, frame: str) -> None:
        await self.ws.send(frame)

    async def echo(self, text: str, message_id: str = "u1") -> None:
        await self.emit({"type": "user_message", "messageId": message_id, "text": text})

    async def delta(self, delta: str, message_id: str = "a1") -> None:
        await self.emit({"type": "message_delta", "messageId": message_id, "delta": delta})

    async def done(self, text: str, message_id: str = "a1", **extra: Any) -> None:
        await self.emit({"type": "message_done", "messageId": message_id, "text": text, **extra})


async def _no_abort(session: Session) -> None:
    return None


class FakeDaemon:
    def __init__(
        self,
        on_send: Script,
        *,
        on_abort: AbortScript = _no_abort,
        token: str = TOKEN,
        send_ready: bool = True,
    ) -> None:
        self.on_send = on_send
        self.on_abort = on_abort
        self.token = token
        self.send_ready = send_ready
        self.sessions: list[Session] = []
        self.tasks: set[asyncio.Task[None]] = set()
        self.url = ""
        self._server: Any = None

    async def __aenter__(self) -> FakeDaemon:
        self._server = await serve(self._handle, "127.0.0.1", 0)
        port = next(iter(self._server.sockets)).getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/ws"
        return self

    async def __aexit__(self, *exc: object) -> None:
        for task in list(self.tasks):
            task.cancel()
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()

    @property
    def session(self) -> Session:
        assert len(self.sessions) == 1, f"expected one connection, got {len(self.sessions)}"
        return self.sessions[0]

    async def _handle(self, ws: ServerConnection) -> None:
        session = Session(ws)
        self.sessions.append(session)
        try:
            hello = json.loads(await ws.recv())
            session.received.append(hello)
            if hello.get("protocol") != 1:
                await ws.close(4400, "unsupported protocol")
                return
            if hello.get("token") != self.token:
                await ws.close(4401, "unauthorized")
                return
            if not self.send_ready:
                await ws.wait_closed()
                return
            await session.emit(
                {"type": "ready", "agentState": "idle", "conversationId": "c1", "model": "m"}
            )
            async for raw in ws:
                frame = json.loads(raw)
                session.received.append(frame)
                if frame.get("type") == "send":
                    self._spawn(self.on_send(session, frame.get("text", "")))
                elif frame.get("type") == "abort":
                    self._spawn(self.on_abort(session))
        except ConnectionClosed:
            pass
        finally:
            session.closed.set()

    def _spawn(self, coro: Awaitable[None]) -> None:
        async def run() -> None:
            with contextlib.suppress(ConnectionClosed):
                await coro

        task = asyncio.ensure_future(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


def free_port() -> int:
    """A localhost port with nothing listening on it."""

    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
