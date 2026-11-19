"""F2 switches to the developer view, fed by the same backend events as the user view."""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import Any

from conftest import fake_server, frame, wait_for

from jarvis.tui import JarvisApp

TS = 1_763_550_000.0
DEVICE = "esp32-s3"


def _turn(turn_id: str, ts: float, stages: list[tuple[str, float, str | None]]) -> list[bytes]:
    frames = [frame({"type": "turn_started", "turn_id": turn_id, "device_id": DEVICE, "ts": ts})]
    for stage, ms, text in stages:
        payload: dict[str, Any] = {"type": "turn_stage", "turn_id": turn_id, "stage": stage, "ms": ms}
        if text is not None:
            payload["text"] = text
        frames.append(frame(payload))
    return frames


def _done(turn_id: str, lane: str, total_ms: float, reply: str) -> bytes:
    return frame(
        {"type": "turn_done", "turn_id": turn_id, "lane": lane, "total_ms": total_ms, "reply_text": reply}
    )


FRAMES = [
    frame(
        {
            "type": "esp32_snapshot",
            "devices": [
                {"device_id": DEVICE, "state": "speaking", "rssi": -52, "ip": "192.168.1.40", "online": True}
            ],
        }
    ),
    # Within target: fast lane, 0.6 s of 1.5 s, with a catalogue action.
    *_turn("e4a2f8f4aa", TS, [("stt", 310, "pausá la música"), ("route", 4, None)]),
    frame({"type": "action", "device_id": DEVICE, "action": {"type": "media_key", "value": "play_pause"}}),
    _done("e4a2f8f4aa", "fast", 600, "Listo."),
    # Over target: chat lane, 4.6 s of 4.0 s.
    *_turn(
        "b90c3e77aa",
        TS + 60,
        [("stt", 450, "resumime el último correo"), ("route", 7, None), ("agent", 3600, None), ("tts", 550, None)],
    ),
    _done("b90c3e77aa", "chat", 4600, "Tenés un correo de Jaime."),
    # Failed at stt.
    *_turn("c77d10aaaa", TS + 120, []),
    frame({"type": "turn_error", "turn_id": "c77d10aaaa", "stage": "stt", "message": "no se entendió el audio"}),
    # Live: still in route.
    *_turn("a13f5b9eaa", TS + 180, [("stt", 300, "abrí Firefox")]),
]


def _screen_text(app: JarvisApp) -> str:
    # The SVG splits a row at every change of style; dropping the tags joins it again.
    return html.unescape(re.sub(r"<[^>]+>", "", app.export_screenshot())).replace("\xa0", " ")


def _app(tmp_path: Path, port: int) -> JarvisApp:
    return JarvisApp(
        url="ws://127.0.0.1:9/ws",
        token_path=tmp_path / "missing-token",
        backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
        backend_token="t",
    )


def test_f2_switches_to_the_developer_view_and_back(tmp_path: Path, caplog: Any) -> None:
    async def handler(socket: Any) -> None:
        for item in FRAMES:
            await socket.send(item)
        await socket.wait_closed()

    async def scenario() -> tuple[str, str, str]:
        async with fake_server(handler) as port:
            app = _app(tmp_path, port)
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(lambda: "a13f5b9e" in _screen_text(app), timeout=5.0)
                await pilot.pause()
                user = _screen_text(app)
                await pilot.press("f2")
                await pilot.pause()
                developer = _screen_text(app)
                await pilot.press("f2")
                await pilot.pause()
                return user, developer, _screen_text(app)

    user, developer, back = asyncio.run(scenario())

    # The window starts in the user view, which offers the key in its footer.
    assert "BACKEND CONNECTED" in user and "VOICE TURNS" not in user
    assert "f2" in user and "Developer" in user

    # Device and connections band.
    assert DEVICE in developer and "SPEAKING" in developer
    assert "-52 dBm" in developer and "192.168.1.40" in developer
    assert "backend connected" in developer
    # The live turn: stt done, route live, lane unknown until turn_done.
    assert "LIVE TURN" in developer and "a13f5b9e" in developer
    assert "routing" in developer and "lane —" in developer
    # The table: one within target, one over target, one failed, newest first.
    assert "VOICE TURNS" in developer and "3 finished" in developer
    assert "1 over target" in developer and "1 failed" in developer
    assert "pausá la música" in developer and "0.6 s" in developer
    assert "media_key play_pause" in developer and "Listo." in developer
    assert "resumime el último correo" in developer
    assert "4.6 s" in developer and "+0.6 s" in developer
    assert "failed at stt · no se entendió el audio" in developer
    assert developer.index("c77d10aa") < developer.index("b90c3e77") < developer.index("e4a2f8f4")
    # Lanes and the rest of the screen.
    assert "LANES" in developer and "all within" in developer and "1 over by 0.6 s" in developer
    assert "AGENT" in developer and "PC ACTIONS" in developer and "CONVERSATION" in developer
    assert "f2" in developer and "user view" in developer and "over target" in developer

    # F2 again shows the user view, unchanged.
    assert "BACKEND CONNECTED" in back and "VOICE TURNS" not in back
    assert [record.getMessage() for record in caplog.records if record.levelname == "ERROR"] == []


def test_developer_view_without_data_shows_empty_states(tmp_path: Path) -> None:
    async def handler(socket: Any) -> None:
        await socket.wait_closed()

    async def scenario(size: tuple[int, int]) -> str:
        async with fake_server(handler) as port:
            app = _app(tmp_path, port)
            async with app.run_test(size=size) as pilot:
                await pilot.press("f2")
                await wait_for(lambda: "backend connected" in _screen_text(app), timeout=5.0)
                await pilot.pause()
                return _screen_text(app)

    screen = asyncio.run(scenario((140, 40)))
    assert "no device yet" in screen
    assert "no live turn" in screen
    assert "no voice turns yet" in screen
    assert "0 finished" in screen
    assert "no messages yet" in screen and "nothing yet" in screen
    # No demo data leaks in.
    assert "SPEAKING" not in screen and "claude-sonnet" not in screen

    for size in ((100, 30), (80, 24), (200, 50)):
        small = asyncio.run(scenario(size))
        assert "VOICE TURNS" in small
