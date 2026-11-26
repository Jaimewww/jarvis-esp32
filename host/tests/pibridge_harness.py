"""The Pi bridge on a free local port with a fake Pi, and real ``JarvisClient``s to talk to it."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from fake_pi import FakeLauncher
from websockets.asyncio.client import ClientConnection, connect

from jarvis.client import ConnectionStatus, Event, JarvisClient
from jarvis.model import HostModel
from jarvis.pibridge.server import BridgeServer, Hub
from jarvis.pibridge.token import load_or_create_token
from jarvis.pibridge.turns import Bridge
from jarvis.turntrace import TurnTrace

T = TypeVar("T")


def run(coro: Awaitable[T], timeout: float = 15) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(bounded())


async def wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "condition was not met in time"
        await asyncio.sleep(0.01)


@dataclass
class Running:
    url: str
    token_path: Path
    token: str
    bridge: Bridge
    hub: Hub
    server: BridgeServer
    launcher: FakeLauncher


@asynccontextmanager
async def bridge_at(launcher: FakeLauncher, token_path: Path, **options: Any) -> AsyncIterator[Running]:
    hub = Hub()
    bridge = Bridge(launcher, hub.broadcast, model="openai/test-model", **options)
    token = load_or_create_token(token_path)
    server = BridgeServer(token=token, agent=bridge, hub=hub)
    port = await server.start("127.0.0.1", 0)
    try:
        yield Running(f"ws://127.0.0.1:{port}/ws", token_path, token, bridge, hub, server, launcher)
    finally:
        await server.close()
        await bridge.close()


@dataclass
class Probe:
    """A real ``JarvisClient`` whose events also feed a ``HostModel`` (the TUI's state)."""

    client: JarvisClient
    events: list[Event] = field(default_factory=list)
    traces: list[TurnTrace] = field(default_factory=list)
    host: HostModel = field(init=False)
    task: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        self.host = HostModel(on_turn=self.traces.append)

    def of(self, kind: type[T]) -> list[T]:
        return [e for e in self.events if isinstance(e, kind)]


@asynccontextmanager
async def jarvis(running: Running) -> AsyncIterator[Probe]:
    probe: Probe

    def on_event(event: Event) -> None:
        probe.events.append(event)
        probe.host.apply_daemon(event)

    def on_status(status: ConnectionStatus, detail: str) -> None:
        probe.host.set_daemon_status(status, detail)

    client = JarvisClient(on_event, on_status, url=running.url, token_path=running.token_path)
    probe = Probe(client)
    probe.task = asyncio.ensure_future(client.run())
    try:
        await wait_for(lambda: client.connected)
        yield probe
    finally:
        await client.close()
        await probe.task


@asynccontextmanager
async def raw(running: Running) -> AsyncIterator[ClientConnection]:
    """A bare WebSocket past the hello; ``ready`` is already read."""

    async with connect(running.url) as socket:
        await socket.send(json.dumps({"type": "hello", "token": running.token, "protocol": 1}))
        assert json.loads(await socket.recv())["type"] == "ready"
        yield socket


async def reply_to(socket: ClientConnection, request: dict[str, Any], kind: str, timeout: float = 3) -> dict[str, Any]:
    """Send ``request`` and return the first frame of type ``kind`` (or ``error``)."""

    await socket.send(json.dumps(request))
    while True:
        frame = json.loads(await asyncio.wait_for(socket.recv(), timeout))
        if frame.get("type") in (kind, "error"):
            return frame
