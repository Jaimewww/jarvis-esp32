"""The window shows the ESP32 state and the voice turns the backend reports.

The user view says it in plain words; the developer detail (state names, dBm, address,
turn id, lane, time) is on the developer view, one f2 away.
"""

from __future__ import annotations

import asyncio
import html
import re
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


def _plain(app: JarvisApp) -> str:
    # The SVG splits a row at every change of style and drops runs of spaces between
    # them; without tags and spaces a phrase can still be found.
    return re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", app.export_screenshot())))


def _has(screen: str, phrase: str) -> bool:
    return re.sub(r"\s+", "", phrase) in screen


def test_shows_device_state_and_turns_from_the_backend(tmp_path: Path, caplog: Any) -> None:
    async def handler(socket: Any) -> None:
        for item in FRAMES:
            await socket.send(item)
        await socket.wait_closed()

    async def scenario() -> tuple[str, str]:
        async with fake_server(handler) as port:
            app = JarvisApp(
                # Nothing listens here and the token file is missing: the daemon side
                # stays disconnected, which must not hide the backend side.
                url="ws://127.0.0.1:9/ws",
                token_path=tmp_path / "missing-token",
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
            )
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(lambda: "Entendido" in _screen_text(app), timeout=5.0)
                await pilot.pause()
                user = _plain(app)
                await pilot.press("f2")
                await pilot.pause()
                return user, _plain(app)

    user, developer = asyncio.run(scenario())

    # User view: the device in plain words, the exchange and what it did on the PC.
    assert _has(user, "Voice device esp32-s3")
    assert _has(user, "Listening · talk any time")
    assert _has(user, "connected to this PC") and _has(user, "good signal")
    assert _has(user, "said ◎") and _has(user, "you spoke")  # C2 sent no transcript
    assert _has(user, "◆ Entendido")
    assert _has(user, "on your PC ✓ played or paused the music")
    for detail in ("LISTENING", "-52dBm", "192.168.1.40", "abc12345", "media_key", "812", "0.8s"):
        assert detail not in user, detail
    # Developer view: the same events with their detail.
    assert _has(developer, "esp32-s3 ◐ LISTENING")
    assert _has(developer, "-52 dBm") and "192.168.1.40" in developer
    assert _has(developer, "● backend connected")
    assert "abc12345" in developer
    assert _has(developer, "→ media_key play_pause Entendido")
    assert _has(developer, "chat") and _has(developer, "0.8 s")
    # Closing the window must not report a status to widgets that are already gone.
    assert [record.getMessage() for record in caplog.records if record.levelname == "ERROR"] == []


def _stt_stage(turn_id: str, ms: float, warning: str | None = None) -> bytes:
    payload: dict[str, Any] = {
        "type": "turn_stage",
        "turn_id": turn_id,
        "stage": "stt",
        "ms": ms,
        "text": "hola",
        "ts": 1.0,
    }
    if warning is not None:
        payload["warning"] = warning
    return frame(payload)


def _notifications(frames: list[bytes], tmp_path: Path, caplog: Any) -> list[tuple[str, str]]:
    """Run the window against a backend that sends ``frames``; return its notifications."""

    async def handler(socket: Any) -> None:
        for item in frames:
            await socket.send(item)
        await socket.send(frame({"type": "turn_started", "turn_id": "end", "device_id": "d"}))
        await socket.wait_closed()

    notes: list[tuple[str, str]] = []

    async def scenario() -> None:
        async with fake_server(handler) as port:
            app = JarvisApp(
                url="ws://127.0.0.1:9/ws",
                token_path=tmp_path / "missing-token",
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
            )
            original = app.notify

            def record(message: str, *args: Any, **kwargs: Any) -> None:
                notes.append((message, str(kwargs.get("severity", "information"))))
                original(message, *args, **kwargs)

            app.notify = record  # type: ignore[method-assign]
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(
                    lambda: any(t.turn_id == "end" for t in app._host.turns),  # noqa: SLF001
                    timeout=5.0,
                )
                await pilot.pause()

    asyncio.run(scenario())
    return notes


def test_warns_once_when_the_backend_says_stt_runs_on_cpu(tmp_path: Path, caplog: Any) -> None:
    warning = "STT lento (14850 ms): whisper-server parece correr en CPU (~15 s por turno)"
    frames = [_stt_stage("t1", 14850.0, warning), _stt_stage("t2", 15010.0, warning)]

    with caplog.at_level("WARNING"):
        notes = _notifications(frames, tmp_path, caplog)

    assert notes == [(warning, "warning")]
    assert [r.getMessage() for r in caplog.records if "CPU" in r.getMessage()] == [warning]


def test_gpu_fast_stt_does_not_warn(tmp_path: Path, caplog: Any) -> None:
    notes = _notifications([_stt_stage("t1", 350.0)], tmp_path, caplog)

    assert [note for note in notes if "CPU" in note[0]] == []


def test_help_documents_the_backend_options(capsys: Any) -> None:
    try:
        main(["--help"])
    except SystemExit as stop:
        assert stop.code == 0
    output = capsys.readouterr().out

    assert "--backend-url" in output
    assert "--backend-token" in output
    assert "JARVIS_HOST_TOKEN" in output
