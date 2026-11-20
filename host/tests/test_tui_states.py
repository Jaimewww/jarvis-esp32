"""States found by running the window against the real backend and simulator.

Each test drives the app through the real clients (fake backend, fake or absent
daemon) and reads what is on screen.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import fake_server, frame, wait_for
from test_tui_developer import FRAMES as DEVELOPER_FRAMES

from jarvis.devview import DeveloperView
from jarvis.theme import DEFAULT_DARK, THEMES
from jarvis.tui import JarvisApp, KeysFooter
from jarvis.userview import UserView, big

DEVICE = "esp32-s3"
IDLE = frame(
    {
        "type": "esp32_snapshot",
        "devices": [{"device_id": DEVICE, "state": "idle", "rssi": -50, "ip": "127.0.0.1", "online": True}],
    }
)


def _exchange(turn_id: str, ts: float, heard: str, reply: str) -> list[bytes]:
    return [
        frame({"type": "turn_started", "turn_id": turn_id, "device_id": DEVICE, "ts": ts}),
        frame({"type": "turn_stage", "turn_id": turn_id, "stage": "stt", "ms": 300, "text": heard}),
        frame({"type": "turn_done", "turn_id": turn_id, "lane": "fast", "total_ms": 600, "reply_text": reply}),
    ]


async def _views(
    tmp_path: Path, frames: list[bytes], size: tuple[int, int], ready: str, daemon: Any = None
) -> dict[str, Any]:
    """Run the window with ``frames`` from the backend; the user view, then f2."""

    async def backend(socket: Any) -> None:
        for item in frames:
            await socket.send(item)
        await socket.wait_closed()

    async def idle(socket: Any) -> None:
        await socket.wait_closed()

    token = tmp_path / "token"
    if daemon is not None:
        token.write_text("daemon-token\n", encoding="utf-8")
    async with fake_server(backend) as port, fake_server(daemon or idle) as daemon_port:
        app = JarvisApp(
            # Without a daemon the token file is missing: Gentle Dot was never started.
            url=f"ws://127.0.0.1:{daemon_port if daemon else 9}/ws",
            token_path=token if daemon else tmp_path / "missing-token",
            backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
            backend_token="t",
            animate=False,
        )
        async with app.run_test(size=size) as pilot:
            await wait_for(
                lambda: ready in app.query_one(UserView).render().plain
                and bool(daemon or app._host.daemon_detail),
                timeout=5.0,
            )
            await pilot.pause()
            out: dict[str, Any] = {
                "user": app.query_one(UserView).render(),
                "user_footer": app.query_one(KeysFooter).render().plain,
                "model": app._host,
            }
            await pilot.press("f2")
            await pilot.pause()
            out["developer"] = app.query_one(DeveloperView).render().plain
            out["developer_footer"] = app.query_one(KeysFooter).render().plain
            return out


def _card(plain: str, width: int) -> list[str]:
    """The Jarvis card's text: the header rows, left of the voice device card."""

    lines = plain.splitlines()
    end = next(i for i, line in enumerate(lines) if "╰" in line) + 1
    if width >= 100:
        cut = lines[0].index("╭─ Voice device")
        return [line[:cut] for line in lines[:end]]
    return lines[:end]


# --- 1. Daemon down: one short, complete message per line ------------------------


@pytest.mark.parametrize("size", [(140, 40), (120, 30), (110, 30), (100, 30), (80, 24), (60, 20)])
def test_daemon_down_card_says_one_complete_thing_per_line(tmp_path: Path, size: tuple[int, int]) -> None:
    views = asyncio.run(_views(tmp_path, [IDLE], size, "talk any time"))
    card = _card(views["user"].plain, size[0])
    text = "\n".join(card)
    assert "…" not in text  # never an ellipsis mid-instruction
    assert "Gentle Dot isn't set up yet" in text
    assert "start it once to type here" in text
    # The second line does not repeat the first.
    assert text.count("Gentle Dot") == 1


# --- 4. One agent availability for both views --------------------------------------


def test_agent_availability_is_one_state_in_both_views(tmp_path: Path) -> None:
    views = asyncio.run(_views(tmp_path, [IDLE], (140, 40), "talk any time"))
    user, developer = views["user"].plain, views["developer"]
    assert views["model"].agent_status == "offline"
    # The user hero: OFFLINE in block letters, never READY with a green dot.
    for row in big("OFFLINE"):
        assert row in user
    assert big("READY")[1] not in user
    hero = next(line for line in user.splitlines() if big("OFFLINE")[1] in line)
    assert "○" in hero and "●" not in hero
    # The voice device keeps its own readiness.
    assert "Ready" in user and "talk any time" in user
    # The developer view shows the same state.
    agent = next(line for line in developer.splitlines() if re.search(r"│\s+state\s", line))
    assert "OFFLINE" in agent and "STARTING" not in developer


# --- 2. The thread starts under "Today" and grows down -----------------------------


def test_few_exchanges_sit_right_under_today(tmp_path: Path) -> None:
    now = time.time()
    frames = [IDLE, *_exchange("t1", now - 60, "pausá la música", "Listo."),
              *_exchange("t2", now - 30, "subí el volumen", "Volumen al 60.")]
    views = asyncio.run(_views(tmp_path, frames, (140, 40), "Volumen al 60."))
    lines = views["user"].plain.splitlines()
    header_end = next(i for i, line in enumerate(lines) if "╰" in line)
    today = next(i for i, line in enumerate(lines) if "Today" in line)
    assert today == header_end + 2  # one blank row under the header cards
    first = next(i for i, line in enumerate(lines) if "pausá la música" in line)
    assert first == today + 2  # the rail row, then the first exchange
    last = next(i for i, line in enumerate(lines) if "Volumen al 60." in line)
    # Free rows are left below the thread, not above it.
    assert all(not line.strip() for line in lines[last + 1 :])
    assert all(line.strip() for line in lines[today : last + 1] if line.strip() != "│")


def test_no_exchanges_show_a_centred_hint(tmp_path: Path) -> None:
    views = asyncio.run(_views(tmp_path, [IDLE], (140, 40), "talk any time"))
    lines = views["user"].plain.splitlines()
    today = next(i for i, line in enumerate(lines) if "Today" in line)
    region = lines[today + 1 :]
    hint = [i for i, line in enumerate(region) if line.strip()]
    assert len(hint) == 2
    first, second = (region[i] for i in hint)
    assert first.strip() == "Nothing here yet."
    # Gentle Dot is not running, so the hint only offers the voice device.
    assert second.strip() == "Talk to the voice device to start."
    for line in (first, second):
        left, right = len(line) - len(line.lstrip()), len(line) - len(line.rstrip())
        assert abs(left - right) <= 1, line
    above, below = hint[0], len(region) - 1 - hint[-1]
    assert abs(above - below) <= 1


# --- 3. A live voice turn says what is happening -----------------------------------


def test_live_voice_turn_says_what_is_happening(tmp_path: Path) -> None:
    frames = [
        IDLE,
        frame({"type": "turn_started", "turn_id": "live1", "device_id": DEVICE, "ts": time.time()}),
        frame({"type": "turn_stage", "turn_id": "live1", "stage": "stt", "ms": 300, "text": "abrí Firefox"}),
    ]
    views = asyncio.run(_views(tmp_path, frames, (140, 40), "abrí Firefox"))
    text = views["user"]
    lines = text.plain.splitlines()
    assert not any(re.search(r"◆\s*▍", line) for line in lines)
    line = next(line for line in lines if "Thinking…" in line)
    assert "route" not in line and "live1" not in line
    start = text.plain.index("Thinking…")
    live = THEMES[DEFAULT_DARK].live
    colours = {
        str(span.style.color.triplet.hex) if span.style.color and span.style.color.triplet else None  # type: ignore[union-attr]
        for span in text.spans
        if span.start <= start < span.end
    }
    assert live in colours


# --- 5. The developer view at 100 columns drops whole columns ----------------------


LANE_WORDS = {"fast", "chat", "task", "turn", "turns", "median", "worst", "target", "s", "all", "within",
              "over", "by", "no", "yet", "acknowledge", "only"}


def test_developer_view_at_100_columns_drops_whole_columns(tmp_path: Path) -> None:
    views = asyncio.run(_views(tmp_path, DEVELOPER_FRAMES, (100, 30), "abrí Firefox"))
    lines = views["developer"].splitlines()
    lanes = lines[next(i for i, line in enumerate(lines) if "Lanes" in line) + 1 :][:3]
    for line in lanes:
        left = line.split("│")[0]  # the lanes table, left of the agent card
        assert set(re.findall(r"[a-z]+", left)) <= LANE_WORDS, left
    # The stage boxes all follow one rule: a word shows in every box of its kind or in none.
    boxes = next(line for line in lines if "route" in line and "agent" in line)
    assert boxes.count("waiting") in (0, 2)  # agent and tts are both waiting


# --- 6. The footer keeps the whole legend at 140 columns ---------------------------


LEGEND = ("connected", "live", "waiting", "within", "over target", "failed", "lane target")


def test_footer_keeps_the_whole_legend_at_140_columns(tmp_path: Path) -> None:
    views = asyncio.run(_views(tmp_path, [IDLE], (140, 40), "talk any time"))
    for footer in (views["user_footer"], views["developer_footer"]):
        assert re.search(r"f2 view\s+f3 mode", footer), footer
    for word in LEGEND:
        assert word in views["developer_footer"], word


def test_footer_drops_whole_legend_entries_below_140_columns(tmp_path: Path) -> None:
    views = asyncio.run(_views(tmp_path, [IDLE], (120, 30), "talk any time"))
    footer = views["developer_footer"]
    assert "f2 view" in footer and "f3 mode" in footer
    shown = [word for word in LEGEND if word in footer]
    # Whole entries only, the least telling ones first: connected and waiting go.
    assert shown == ["live", "within", "over target", "failed", "lane target"]
    legend = footer[footer.index("◐") :]
    assert set(re.findall(r"[a-z]+", legend)) <= {w for entry in LEGEND for w in entry.split()}


# --- 7. No AI account in Gentle Dot: the daemon's own message stays on screen ---------

NO_ACCOUNT = "Connect an AI account first: open Accounts or type /login."


def test_typing_without_an_ai_account_shows_the_daemons_message(tmp_path: Path) -> None:
    """As the real daemon (upstream f97d56d) does it: the send is refused, nothing echoed."""

    sent: list[dict[str, Any]] = []

    async def daemon(socket: Any) -> None:
        seq = 0

        async def emit(payload: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            await socket.send(json.dumps({**payload, "seq": seq}))

        async for raw in socket:
            message = json.loads(raw)
            sent.append(message)
            if message.get("type") == "hello":
                await emit({"type": "ready", "agentState": "idle", "model": "unknown",
                            "features": {"conversations": False}})
            elif message.get("type") == "send":
                await emit({"type": "error", "code": "no_account", "message": NO_ACCOUNT})

    async def backend(socket: Any) -> None:
        await socket.send(IDLE)
        await socket.wait_closed()

    async def scenario() -> tuple[str, str]:
        token = tmp_path / "token"
        token.write_text("daemon-token\n", encoding="utf-8")
        async with fake_server(backend) as port, fake_server(daemon) as daemon_port:
            app = JarvisApp(
                url=f"ws://127.0.0.1:{daemon_port}/ws",
                token_path=token,
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
            )
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(lambda: app._host.agent_status == "idle", timeout=5.0)
                await pilot.click("#prompt")
                await pilot.press(*"hola", "enter")
                await wait_for(lambda: app._host.agent_notice is not None, timeout=5.0)
                await pilot.pause()
                user = app.query_one(UserView).render().plain
                await pilot.press("f2")
                await pilot.pause()
                return user, app.query_one(DeveloperView).render().plain

    user, developer = asyncio.run(scenario())
    assert {"type": "send", "text": "hola"} in sent
    # Never signs in (the client's read-only ``auth_list`` on connect is not a sign-in).
    assert not any(m.get("type") in ("auth_login", "auth_reply") for m in sent)
    assert NO_ACCOUNT in user  # whole, on its own row under the header
    assert "talk or type" not in user
    # Developer view: the Agent card's "needs" row, wrapped to the card.
    lines = developer.splitlines()
    i = next(n for n, line in enumerate(lines) if " needs " in line)
    col = lines[i].index(" needs ") + len(" needs ")
    wrapped = " ".join(line[col:].replace("│", " ") for line in lines[i : i + 3])
    assert " ".join(wrapped.split()).startswith(NO_ACCOUNT)
