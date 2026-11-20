"""A scripted ``jarvis-input`` control socket (S13), for the TUI's control client.

It serves ``$XDG_RUNTIME_DIR/jarvis-control/control.sock`` (always a temp dir in tests),
JSON by lines: ``grant``/``revoke``/``status`` answer once; ``subscribe`` stays open and
gets ``state`` and ``confirm`` events; ``answer`` arrives on that same connection.
Permission state is in memory, like the real helper: a restart revokes it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeHelper:
    def __init__(self, runtime: Path, clock: Callable[[], datetime] | None = None,
                 ack_subscribe: bool = False) -> None:
        self.path = runtime / "jarvis-control" / "control.sock"
        self.clock = clock or (lambda: datetime.now(UTC))
        self.ack_subscribe = ack_subscribe
        self.received: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.subscribers: list[asyncio.StreamWriter] = []
        self.expires_at: datetime | None = None
        self.refuse: str | None = None  # an error answer to every request
        self._server: asyncio.base_events.Server | None = None

    @property
    def enabled(self) -> bool:
        return self.expires_at is not None and self.clock() < self.expires_at

    def _state(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "expires_at": iso(self.expires_at) if self.enabled and self.expires_at else None}

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))

    async def stop(self) -> None:
        """Like the helper exiting: the subscribers drop and the permission is gone."""

        for writer in list(self.subscribers):
            writer.close()
        self.subscribers.clear()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 1.0)
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)
        self.expires_at = None

    async def __aenter__(self) -> FakeHelper:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def broadcast(self, event: dict[str, Any]) -> None:
        line = (json.dumps(event) + "\n").encode()
        for writer in list(self.subscribers):
            with contextlib.suppress(Exception):
                writer.write(line)
                await writer.drain()

    async def push_state(self) -> None:
        await self.broadcast({"event": "state", **self._state()})

    async def push_confirm(self, id: str = "c1", title: str = "Press Return in Brave?",
                           message: str = "pc_key Return in brave-browser", timeout_s: float = 30) -> None:
        await self.broadcast({"event": "confirm", "id": id, "title": title, "message": message,
                              "timeout_s": timeout_s})

    async def drop_subscribers(self) -> None:
        for writer in list(self.subscribers):
            writer.close()
        self.subscribers.clear()

    def ops(self, op: str) -> list[dict[str, Any]]:
        return [m for m in self.received if m.get("op") == op]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def send(payload: dict[str, Any]) -> None:
            writer.write((json.dumps(payload) + "\n").encode())

        try:
            while line := await reader.readline():
                message = json.loads(line)
                self.received.append(message)
                op = message.get("op")
                if op == "answer":
                    self.answers.append(message)
                    continue
                if self.refuse is not None:
                    send({"ok": False, "error": self.refuse})
                elif op == "subscribe":
                    self.subscribers.append(writer)
                    if self.ack_subscribe:
                        send({"ok": True})
                        send({"event": "state", **self._state()})
                elif op == "grant":
                    self.expires_at = self.clock() + timedelta(minutes=min(30, int(message.get("minutes", 30))))
                    send({"ok": True, "expires_at": iso(self.expires_at)})
                    await self.push_state()
                elif op == "revoke":
                    self.expires_at = None
                    send({"ok": True})
                    await self.push_state()
                elif op == "status":
                    send({"ok": True, **self._state()})
                else:
                    send({"ok": False, "error": f"unknown op {op!r}"})
                await writer.drain()
        except (ConnectionError, ValueError):
            pass
        finally:
            if writer in self.subscribers:
                self.subscribers.remove(writer)
            writer.close()
