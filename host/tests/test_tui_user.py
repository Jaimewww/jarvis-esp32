"""The user view, fed through the real clients by a fake backend and a fake daemon."""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import fake_server, frame, wait_for

from jarvis import tui, userview
from jarvis.tui import JarvisApp
from jarvis.userview import UserView

DEVICE = "esp32-s3"


def _screen_text(app: JarvisApp) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", app.export_screenshot())).replace("\xa0", " ")


def _flat(text: str) -> str:
    # The SVG export drops runs of spaces between differently styled cells.
    return re.sub(r"\s+", "", text)


def _has(screen: str, phrase: str) -> bool:
    return _flat(phrase) in _flat(screen)


def _backend_frames(now: float) -> list[bytes]:
    return [
        frame(
            {
                "type": "esp32_snapshot",
                "devices": [
                    {"device_id": DEVICE, "state": "listening", "rssi": -52, "ip": "192.168.1.40", "online": True}
                ],
            }
        ),
        # A voice exchange with a catalogue action.
        frame({"type": "turn_started", "turn_id": "abc12345", "device_id": DEVICE, "ts": now - 60}),
        frame({"type": "turn_stage", "turn_id": "abc12345", "stage": "stt", "ms": 310, "text": "pausá la música"}),
        frame({"type": "action", "device_id": DEVICE, "action": {"type": "media_key", "value": "play_pause"}}),
        frame({"type": "turn_done", "turn_id": "abc12345", "lane": "fast", "total_ms": 812.4, "reply_text": "Listo."}),
        # A failed one.
        frame({"type": "turn_started", "turn_id": "def67890", "device_id": DEVICE, "ts": now - 50}),
        frame({"type": "turn_error", "turn_id": "def67890", "stage": "stt", "message": "no se entendió el audio"}),
    ]


def _daemon_frames() -> list[dict[str, Any]]:
    return [
        {"type": "agent_state", "state": "working"},
        # The daemon echoes the voice turn's words (the agent behind it is Gentle Dot).
        {"type": "user_message", "messageId": "echo", "text": "Pausá la música."},
        {"type": "user_message", "messageId": "u1", "text": "Abrí el informe de redes"},
        {"type": "message_done", "messageId": "m1", "text": "El informe tiene 14 páginas."},
        {"type": "activity", "messageId": "m1",
         "activity": {"id": "a1", "kind": "open_app", "title": "opened Okular", "status": "done"}},
        {"type": "user_message", "messageId": "u2", "text": "Completá las conclusiones"},
        {"type": "message_delta", "messageId": "m2", "delta": "Estoy redactando el cierre"},
        {"type": "activity", "messageId": "m2",
         "activity": {"id": "a2", "kind": "type", "title": "writing in the document", "status": "running"}},
        {"type": "queue", "steering": [], "followUp": ["después"]},
    ]


async def _daemon(socket: Any) -> None:
    await socket.recv()  # hello
    await socket.send(json.dumps({"type": "ready", "seq": 1, "agentState": "idle", "model": "claude-sonnet-5-5"}))
    for _ in range(3):  # get_history, list_conversations, auth_list
        await socket.recv()
    for seq, payload in enumerate(_daemon_frames(), start=2):
        await socket.send(json.dumps({**payload, "seq": seq}))
    await socket.wait_closed()


def test_user_view_through_the_real_event_path(tmp_path: Path, caplog: Any) -> None:
    token = tmp_path / "token"
    token.write_text("daemon-token\n", encoding="utf-8")
    now = time.time()

    async def backend(socket: Any) -> None:
        for item in _backend_frames(now):
            await socket.send(item)
        await socket.wait_closed()

    async def scenario() -> tuple[str, str]:
        async with fake_server(backend) as backend_port, fake_server(_daemon) as daemon_port:
            app = JarvisApp(
                url=f"ws://127.0.0.1:{daemon_port}/ws",
                token_path=token,
                backend_url=f"ws://127.0.0.1:{backend_port}/api/v1/ws",
                backend_token="t",
                animate=False,
            )
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(
                    lambda: _has(_screen_text(app), "1 more message waiting its turn")
                    and _has(_screen_text(app), "Couldn't hear you"),
                    timeout=5.0,
                )
                await pilot.pause()
                plain = app.query_one(UserView).render().plain
                return _screen_text(app), plain

    screen, plain = asyncio.run(scenario())
    lines = plain.splitlines()

    # Header: Jarvis is working on the typed message, one more waits; the voice device.
    assert _has(screen, "on the message you typed")
    assert _has(screen, "1 more message waiting its turn")
    assert "Voice device" in screen and DEVICE in screen
    assert "Listening" in screen and _has(screen, "talk any time")
    assert _has(screen, "connected to this PC") and _has(screen, "good signal")
    # Voice exchange with its action, shown once although the daemon echoed it.
    assert sum(1 for line in lines if "pausá la música" in line.casefold()) == 1
    said = next(line for line in lines if "pausá la música" in line)
    assert re.match(r"\s+\d\d:\d\d\s+said\s+◎\s{20,}pausá la música\s+$", said)
    assert any(line.strip() == "│ ◆ Listo." for line in lines)
    assert any("on your PC  ✓ played or paused the music" in line for line in lines)
    # Failed exchange with what to do next.
    assert any("✗ Couldn't hear you. Say it again." in line for line in lines)
    assert "the voice device is still listening, so just talk again" in plain
    # Typed exchange, then the live one with its running action and how to stop it.
    typed = next(line for line in lines if "Abrí el informe de redes" in line)
    assert re.match(r"\s+\d\d:\d\d\s+typed\s+▸", typed)
    assert any(line.strip() == "│ ◆ El informe tiene 14 páginas." for line in lines)
    assert any("✓ opened Okular" in line for line in lines)
    assert any("┃ ◆ Estoy redactando el cierre▍" in line for line in lines)
    assert any("┃" in line and "◐ writing in the document    ctrl+a stops it" in line for line in lines)
    # The thread is in time order.
    assert plain.index("pausá la música") < plain.index("Couldn't hear you") < plain.index("Abrí el informe")
    # No developer detail in this view.
    for detail in ("abc12345", "def67890", "-52 dBm", "192.168.1.40", "812", "media_key", "fast",
                   "stt", "claude-sonnet", "daemon", "backend"):
        assert detail not in plain, detail
    # Input row and the one footer.
    assert "Message Jarvis" in screen and "▸" in screen
    assert _has(screen, "enter send") and _has(screen, "f2 developer view")
    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == []


def test_animation_runs_while_busy_and_stops_on_close(tmp_path: Path, caplog: Any) -> None:
    async def backend(socket: Any) -> None:
        await socket.send(frame({"type": "turn_started", "turn_id": "t1", "device_id": DEVICE}))
        await socket.wait_closed()

    async def scenario() -> tuple[int, bool, bool]:
        async with fake_server(backend) as port:
            app = JarvisApp(
                url="ws://127.0.0.1:9/ws",
                token_path=tmp_path / "missing-token",
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
            )
            async with app.run_test(size=(140, 40)) as pilot:
                view = app.query_one(UserView)
                await wait_for(lambda: view.frame >= 3, timeout=5.0)
                running = view.animating
                await pilot.press("ctrl+q")
            return view.frame, running, view.animating

    frames, running, after = asyncio.run(scenario())
    assert frames >= 3 and running and not after
    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == []


def test_animation_can_be_disabled_for_stable_screens(tmp_path: Path) -> None:
    async def backend(socket: Any) -> None:
        await socket.send(frame({"type": "turn_started", "turn_id": "t1", "device_id": DEVICE}))
        await socket.wait_closed()

    async def scenario() -> tuple[int, bool]:
        async with fake_server(backend) as port:
            app = JarvisApp(
                url="ws://127.0.0.1:9/ws",
                token_path=tmp_path / "missing-token",
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
            )
            async with app.run_test(size=(140, 40)) as pilot:
                view = app.query_one(UserView)
                await pilot.pause(0.5)
                return view.frame, view.animating

    assert asyncio.run(scenario()) == (0, False)


@pytest.mark.parametrize("size", [(139, 40), (110, 30), (90, 24), (80, 24), (60, 20)])
def test_user_view_below_140_columns_does_not_crash(tmp_path: Path, size: tuple[int, int]) -> None:
    now = time.time()

    async def backend(socket: Any) -> None:
        for item in _backend_frames(now):
            await socket.send(item)
        await socket.wait_closed()

    async def scenario() -> str:
        async with fake_server(backend) as port:
            app = JarvisApp(
                url="ws://127.0.0.1:9/ws",
                token_path=tmp_path / "missing-token",
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
            )
            async with app.run_test(size=size) as pilot:
                await wait_for(lambda: _has(_screen_text(app), "Couldn't hear you"), timeout=5.0)
                await pilot.press("f3")
                await pilot.pause()
                return _screen_text(app)

    screen = asyncio.run(scenario())
    assert _has(screen, "Voice device") and _has(screen, "Couldn't hear you")
    if size[0] >= 80:
        assert "Listo." in screen


def test_views_hold_no_colour_of_their_own() -> None:
    for module in (userview, tui):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{6}\b", source), module.__name__
        # No named colours in Rich markup either: every colour is a theme token.
        assert not re.search(r"\[(?:bold |dim )?(?:red|green|yellow|cyan|magenta|blue|white)\b", source)
