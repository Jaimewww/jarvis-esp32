"""S2: the unchanged backend ``GentleDotAgent`` works against the bridge (fake Pi underneath)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

import pytest
from fake_pi import FakeLauncher, hang, reply

from jarvis.pibridge.server import BridgeServer, Hub
from jarvis.pibridge.token import load_or_create_token
from jarvis.pibridge.turns import Bridge

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.agents.gentle_dot import GentleDotAgent, GentleDotUnauthorized  # noqa: E402

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(coro, 10)

    return asyncio.run(bounded())


@asynccontextmanager
async def bridge_at(launcher: FakeLauncher, token_path: Path) -> AsyncIterator[str]:
    hub = Hub()
    bridge = Bridge(launcher, hub.broadcast, model="openai/test-model")
    server = BridgeServer(token=load_or_create_token(token_path), agent=bridge, hub=hub)
    port = await server.start("127.0.0.1", 0)
    try:
        yield f"ws://127.0.0.1:{port}/ws"
    finally:
        await server.close()
        await bridge.close()


def test_gentle_dot_agent_gets_the_reply_through_the_bridge(tmp_path: Path) -> None:
    token_path = tmp_path / "token"  # created by the bridge, read by the agent

    async def scenario() -> None:
        launcher = FakeLauncher(reply("Son las ", "cinco. ", "Algo más?"), reply("Sí."))
        async with bridge_at(launcher, token_path) as url:
            agent = GentleDotAgent(url, token_path=token_path)
            first = [chunk async for chunk in agent.reply("qué hora es", turn_id="t1")]
            second = [chunk async for chunk in agent.reply("seguro?", turn_id="t2")]
        assert first == ["Son las cinco.", "Algo más?"]
        assert second == ["Sí."]
        assert not agent.busy
        prompts = [r["message"] for r in launcher.current.written if r["type"] == "prompt"]
        assert prompts == ["qué hora es", "seguro?"]

    run(scenario())


def test_gentle_dot_agent_abort_stops_the_turn_and_the_next_reply_works(tmp_path: Path) -> None:
    token_path = tmp_path / "token"

    async def scenario() -> None:
        launcher = FakeLauncher(hang("Abriendo el navegador. ", "Buscando"), reply("Hecho."))
        async with bridge_at(launcher, token_path) as url:
            agent = GentleDotAgent(url, token_path=token_path)
            chunks: list[str] = []
            async for chunk in agent.reply("abre firefox", turn_id="t1"):
                chunks.append(chunk)
                await agent.abort()
            assert chunks == ["Abriendo el navegador."]  # the cut-off tail is dropped
            assert [r["type"] for r in launcher.current.written] == ["prompt", "abort"]
            after = [chunk async for chunk in agent.reply("hola", turn_id="t2")]
        assert after == ["Hecho."]
        assert len(launcher.launches) == 1

    run(scenario())


def test_gentle_dot_agent_with_a_wrong_token_is_unauthorized(tmp_path: Path) -> None:
    token_path = tmp_path / "token"

    async def scenario() -> None:
        async with bridge_at(FakeLauncher(), token_path) as url:
            agent = GentleDotAgent(url, token="not-the-token")
            with pytest.raises(GentleDotUnauthorized):
                async for _ in agent.reply("hola", turn_id="t1"):
                    pass

    run(scenario())
