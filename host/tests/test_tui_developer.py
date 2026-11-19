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
        animate=False,
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
                # The user view shows the live turn by what was heard, never by its id.
                await wait_for(lambda: "abrí Firefox" in _screen_text(app), timeout=5.0)
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
    assert "Voice turns" not in user
    assert "f2 view" in user

    # Device and connections band.
    assert DEVICE in developer and "SPEAKING" in developer
    assert "-52 dBm" in developer and "192.168.1.40" in developer
    assert "backend connected" in developer
    # Device status and the live turn share one rounded card, the device in its top border.
    # (The SVG export drops runs of spaces, so the border reads "╭─esp32-s3".)
    assert any(re.match(rf"\s*╭─\s*{DEVICE}", line) for line in developer.splitlines())
    assert "╰" in developer and "╯" in developer
    # The live turn: stt done, route live, lane unknown until turn_done; the card's pulse.
    assert "Live turn" in developer and "a13f5b9e" in developer
    assert "routing" in developer and "lane —" in developer
    assert "━" in developer and "──→" in developer
    # The table: one within target, one over target, one failed, newest first.
    assert "Voice turns" in developer and "3 finished" in developer
    assert "1 over target" in developer and "1 failed" in developer
    assert "pausá la música" in developer and "0.6 s" in developer
    assert "→ media_key play_pause" in developer and "Listo." in developer
    assert "resumime el último correo" in developer
    assert "4.6 s" in developer and "+0.6 s" in developer
    assert "failed at stt · no se entendió el audio" in developer
    assert developer.index("c77d10aa") < developer.index("b90c3e77") < developer.index("e4a2f8f4")
    # Lanes and the rest of the screen.
    assert "Lanes" in developer and "all within" in developer and "1 over by 0.6 s" in developer
    assert re.search(r"╭─\s*Agent", developer) and "PC actions" in developer and "Conversation" in developer
    assert "f2 view" in developer and "over target" in developer
    for shouting in ("LIVE TURN", "VOICE TURNS", "LANES", "AGENT", "PC ACTIONS", "CONVERSATION"):
        assert shouting not in developer

    # F2 again shows the user view, unchanged.
    assert back == user
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
        assert "Voice turns" in small


def test_developer_view_marks_jarvis_and_draws_cards_without_textual() -> None:
    from jarvis.client import ConnectionStatus, MessageDeltaEvent, MessageDoneEvent, UserMessageEvent
    from jarvis.devview import render_rows
    from jarvis.model import HostModel

    model = HostModel()
    model.set_daemon_status(ConnectionStatus.CONNECTED)
    model.apply_daemon(UserMessageEvent("u1", "Abrí el informe"))
    model.apply_daemon(MessageDoneEvent("m1", "El informe tiene 14 páginas."))
    model.apply_daemon(UserMessageEvent("u2", "Completá las conclusiones"))
    model.apply_daemon(MessageDeltaEvent("m2", "Estoy redactando"))
    for w, h in ((140, 40), (100, 30), (80, 24), (60, 20)):
        rows = render_rows(model, w, h)
        assert len(rows) == h
        assert all(sum(len(t) for t, _ in row) == w for row in rows), (w, h)
    text = ["".join(t for t, _ in row) for row in render_rows(model, 140, 40)]
    jarvis = [line for line in text if "jarvis" in line]
    # A finished answer has no mark (as in the mockup); the live one the live glyph and cursor.
    assert jarvis[0].lstrip().startswith("jarvis") and "◆" not in jarvis[0]
    assert jarvis[1].lstrip().startswith("◐ jarvis") and "▍" in jarvis[1]
    # The agent card closes on the row above the input.
    assert text[-1].rstrip().endswith("╯") and "╰" in text[-1]
