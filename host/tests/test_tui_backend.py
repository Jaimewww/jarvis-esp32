"""The window shows the ESP32 state and the voice turns the backend reports."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from conftest import fake_server, frame, wait_for

from jarvis.tui import JarvisApp, main

FRAMES = [
    frame(
        {
            "type": "esp32_snapshot",
            "devices": [
                {
                    "device_id": "esp32-s3",
                    "state": "listening",
                    "rssi": -52,
                    "ip": "192.168.1.40",
                    "online": True,
                    "ts": 100.0,
                }
            ],
        }
    ),
    frame({"type": "turn_started", "turn_id": "abc12345", "device_id": "esp32-s3", "ts": 101.0}),
    frame(
        {
            "type": "action",
            "device_id": "esp32-s3",
            "action": {"type": "media_key", "value": "play_pause"},
            "ts": 101.5,
        }
    ),
    frame(
        {
            "type": "turn_done",
            "turn_id": "abc12345",
            "lane": "chat",
            "total_ms": 812.4,
            "reply_text": "Entendido",
            "ts": 102.0,
        }
    ),
]


def _screen_text(app: JarvisApp) -> str:
    return app.export_screenshot().replace("&#160;", " ")


def test_shows_device_state_and_turns_from_the_backend(tmp_path: Path, caplog: Any) -> None:
    async def handler(socket: Any) -> None:
        for item in FRAMES:
            await socket.send(item)
        await socket.wait_closed()

    async def scenario() -> str:
        async with fake_server(handler) as port:
            app = JarvisApp(
                # Nothing listens here and the token file is missing: the daemon side
                # stays disconnected, which must not hide the backend side.
                url="ws://127.0.0.1:9/ws",
                token_path=tmp_path / "missing-token",
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
            )
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(lambda: "Entendido" in _screen_text(app), timeout=5.0)
                await pilot.pause()
                return _screen_text(app)

    screen = asyncio.run(scenario())

    assert "ESP32 esp32-s3 LISTENING" in screen
    assert "-52 dBm" in screen
    assert "192.168.1.40" in screen
    assert "BACKEND CONNECTED" in screen
    assert "turn abc12345 started" in screen
    assert "media_key play_pause" in screen
    assert "chat" in screen and "812 ms" in screen
    assert "Entendido" in screen
    # Closing the window must not report a status to widgets that are already gone.
    assert [record.getMessage() for record in caplog.records if record.levelname == "ERROR"] == []


def test_help_documents_the_backend_options(capsys: Any) -> None:
    try:
        main(["--help"])
    except SystemExit as stop:
        assert stop.code == 0
    output = capsys.readouterr().out

    assert "--backend-url" in output
    assert "--backend-token" in output
    assert "JARVIS_HOST_TOKEN" in output
