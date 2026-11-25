"""The admitted connections and the frames going out to them (S2, S9, S12.1).

Every connection has its own monotonic ``seq`` and its own send queue, drained by its own
task, so a client that stops reading holds up neither the turn nor the other clients:
``broadcast`` only numbers and queues. A client more than ``max_pending`` frames behind
is dropped (close 1008); it reconnects and catches up with ``get_history``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

MAX_PENDING: Final = 1024
CLOSE_TOO_SLOW: Final = 1008

Observer = Callable[[dict[str, Any]], None]


class Connection(Protocol):
    async def send(self, message: str) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass(slots=True)
class _Peer:
    seq: int = 0
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue[str]())
    writer: asyncio.Task[None] | None = None


class Hub:
    def __init__(self, *, max_pending: int = MAX_PENDING) -> None:
        self._max_pending = max_pending
        self._peers: dict[Connection, _Peer] = {}
        self._observers: list[Observer] = []
        self._closing: set[asyncio.Task[None]] = set()

    def observe(self, observer: Observer) -> None:
        """Call ``observer`` with every broadcast frame (the conversation keeps them)."""

        self._observers.append(observer)

    @property
    def size(self) -> int:
        return len(self._peers)

    async def admit(self, ws: Connection, ready: dict[str, Any]) -> None:
        peer = _Peer()
        self._peers[ws] = peer
        peer.writer = asyncio.ensure_future(self._write(ws, peer))
        self._queue(ws, peer, ready)

    def remove(self, ws: Connection) -> None:
        peer = self._peers.pop(ws, None)
        if peer is not None and peer.writer is not None and peer.writer is not asyncio.current_task():
            peer.writer.cancel()

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for observer in self._observers:
            observer(payload)
        for ws, peer in list(self._peers.items()):
            self._queue(ws, peer, payload)

    async def send_to(self, ws: Connection, payload: dict[str, Any]) -> None:
        peer = self._peers.get(ws)
        if peer is not None:
            self._queue(ws, peer, payload)

    def _queue(self, ws: Connection, peer: _Peer, payload: dict[str, Any]) -> None:
        if peer.queue.qsize() >= self._max_pending:
            logger.warning("dropped a client more than %d frames behind", self._max_pending)
            self.remove(ws)
            task = asyncio.ensure_future(self._close(ws))
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)
            return
        peer.seq += 1
        peer.queue.put_nowait(json.dumps({**payload, "seq": peer.seq}))

    async def _write(self, ws: Connection, peer: _Peer) -> None:
        try:
            while True:
                await ws.send(await peer.queue.get())
        except ConnectionClosed:
            self.remove(ws)

    @staticmethod
    async def _close(ws: Connection) -> None:
        with contextlib.suppress(Exception):
            await ws.close(CLOSE_TOO_SLOW, "too slow")
