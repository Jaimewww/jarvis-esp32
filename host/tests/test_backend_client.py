"""The backend channel (contract C2) against a fake WebSocket server."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any

from conftest import fake_server, frame, wait_for
from websockets.asyncio.server import serve

from jarvis.backend import (
    ActionEvent,
    BackendClient,
    Device,
    DeviceEvent,
    SnapshotEvent,
    TurnDone,
    TurnError,
    TurnStage,
    TurnStarted,
)
from jarvis.client import ConnectionStatus

DEVICE = {
    "device_id": "esp32-s3",
    "state": "listening",
    "rssi": -52,
    "ip": "192.168.1.40",
    "online": True,
    "ts": 100.5,
}


async def _collect(frames: list[bytes | str], expected: int) -> tuple[list[Any], list[str]]:
    """Run a client against a server that sends ``frames``; return events and paths."""

    events: list[Any] = []
    paths: list[str] = []

    async def handler(socket: Any) -> None:
        paths.append(socket.request.path)
        for item in frames:
            await socket.send(item)
        await socket.wait_closed()

    async with fake_server(handler) as port:
        client = BackendClient(
            events.append, url=f"ws://127.0.0.1:{port}/api/v1/ws", token="s3cret"
        )
        task = asyncio.create_task(client.run())
        try:
            await wait_for(lambda: len(events) >= expected)
            # Let any frame the client must ignore arrive before the count is checked.
            await asyncio.sleep(0.05)
        finally:
            await client.close()
            await task
    return events, paths


def test_connects_with_token_and_parses_every_contract_event() -> None:
    frames: list[bytes | str] = [
        frame({"type": "esp32_snapshot", "devices": [DEVICE]}),
        frame({"type": "esp32_state", **DEVICE, "state": "idle", "online": False}),
        frame(
            {
                "type": "action",
                "device_id": "esp32-s3",
                "action": {"type": "media_key", "value": "play_pause"},
                "ts": 101.0,
            }
        ),
        frame({"type": "turn_started", "turn_id": "t1", "device_id": "esp32-s3", "ts": 102.0}),
        frame(
            {
                "type": "turn_stage",
                "turn_id": "t1",
                "stage": "stt",
                "ms": 120.5,
                "text": "pon música",
                "ts": 102.2,
            }
        ),
        frame(
            {
                "type": "turn_done",
                "turn_id": "t1",
                "lane": "chat",
                "total_ms": 812.4,
                "reply_text": "En orden, señor.",
                "ts": 103.0,
            }
        ),
        frame(
            {"type": "turn_error", "turn_id": "t2", "stage": "tts", "message": "boom", "ts": 104.0}
        ),
    ]

    events, paths = asyncio.run(_collect(frames, expected=7))

    assert paths == ["/api/v1/ws?token=s3cret"]
    listening = Device("esp32-s3", "listening", -52, "192.168.1.40", True, 100.5)
    assert events == [
        SnapshotEvent((listening,)),
        DeviceEvent(Device("esp32-s3", "idle", -52, "192.168.1.40", False, 100.5)),
        ActionEvent("esp32-s3", "media_key", "play_pause", 101.0),
        TurnStarted("t1", "esp32-s3", 102.0),
        TurnStage("t1", "stt", 120.5, "pon música", 102.2),
        TurnDone("t1", "chat", 812.4, "En orden, señor.", 103.0),
        TurnError("t2", "tts", "boom", 104.0),
    ]


def test_ignores_unknown_types_unknown_fields_and_bad_frames() -> None:
    frames: list[bytes | str] = [
        frame({"type": "something_new", "x": 1}),
        b"not json",
        frame({"type": "turn_started", "turn_id": "t9", "device_id": "d", "ts": 1.0, "extra": 5}),
    ]

    events, _ = asyncio.run(_collect(frames, expected=1))

    assert events == [TurnStarted("t9", "d", 1.0)]


def _run_until_stopped(port_handler: Any, **serve_options: Any) -> list[tuple[Any, str]]:
    """Run a client until ``run`` returns by itself; return its status changes."""

    statuses: list[tuple[Any, str]] = []

    async def scenario() -> None:
        async with serve(port_handler, "127.0.0.1", 0, **serve_options) as server:
            port = server.sockets[0].getsockname()[1]
            client = BackendClient(
                lambda event: None,
                lambda status, detail: statuses.append((status, detail)),
                url=f"ws://127.0.0.1:{port}/api/v1/ws",
                token="wrong-token",
            )
            await asyncio.wait_for(client.run(), 3.0)

    asyncio.run(scenario())
    return statuses


def test_close_code_4401_stops_as_unauthorized_without_leaking_the_token() -> None:
    async def handler(socket: Any) -> None:
        await socket.close(code=4401)

    statuses = _run_until_stopped(handler)

    assert statuses[-1][0] is ConnectionStatus.UNAUTHORIZED
    assert "JARVIS_HOST_TOKEN" in statuses[-1][1]
    assert all("wrong-token" not in detail for _, detail in statuses)


def test_handshake_403_stops_as_unauthorized() -> None:
    """The backend refuses a bad token before accepting, which is an HTTP 403."""

    async def handler(socket: Any) -> None:  # pragma: no cover - never reached
        await socket.wait_closed()

    def refuse(connection: Any, request: Any) -> Any:
        return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")

    statuses = _run_until_stopped(handler, process_request=refuse)

    assert statuses[-1][0] is ConnectionStatus.UNAUTHORIZED
    assert all("wrong-token" not in detail for _, detail in statuses)


def test_reconnects_after_the_backend_drops_the_connection() -> None:
    events: list[Any] = []
    connections = 0

    async def handler(socket: Any) -> None:
        nonlocal connections
        connections += 1
        await socket.send(frame({"type": "esp32_snapshot", "devices": []}))
        if connections == 1:
            await socket.close()
        else:
            await socket.wait_closed()

    async def scenario() -> None:
        async with fake_server(handler) as port:
            client = BackendClient(
                events.append,
                url=f"ws://127.0.0.1:{port}/api/v1/ws",
                token="t",
                backoff=(0.01,),
            )
            task = asyncio.create_task(client.run())
            try:
                await wait_for(lambda: len(events) >= 2)
            finally:
                await client.close()
                await task

    asyncio.run(scenario())

    assert events == [SnapshotEvent(()), SnapshotEvent(())]
    assert connections == 2
