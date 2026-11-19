"""Rules of ``app.ports.Agent`` (contract C3) that every implementation must keep.

Runs the fake agent and the Gentle Dot adapter (against the fake daemon) through
the same checks. ``reset`` is not here: the Gentle Dot protocol v1 has no message
for it yet (see test_gentle_dot.py).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fake_gentle_dot import TOKEN, FakeDaemon, Session

from app.agents.gentle_dot import GentleDotAgent
from app.fakes import FakeAgent
from app.ports import Agent

AgentFactory = Callable[[], Any]


@asynccontextmanager
async def fake_agent() -> AsyncIterator[Agent]:
    yield FakeAgent(chunks=["Hola, señor.", "Todo en orden."])


@asynccontextmanager
async def gentle_dot_agent() -> AsyncIterator[Agent]:
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("Hola, señor. Todo ")
        await s.done("Hola, señor. Todo en orden.")

    async with FakeDaemon(script) as daemon:
        yield GentleDotAgent(daemon.url, token=TOKEN)


AGENTS = {"fake": fake_agent, "gentle_dot": gentle_dot_agent}


def run(coro: Awaitable[None]) -> None:
    asyncio.run(asyncio.wait_for(coro, 10))  # type: ignore[arg-type]


@pytest.mark.parametrize("name", AGENTS)
def test_reply_yields_non_empty_text_chunks(name: str):
    async def scenario() -> None:
        async with AGENTS[name]() as agent:
            stream = agent.reply("qué hora es", turn_id="t1")
            assert isinstance(stream, AsyncIterator)
            chunks = [chunk async for chunk in stream]
            assert chunks == ["Hola, señor.", "Todo en orden."]
            assert all(isinstance(c, str) and c for c in chunks)

    run(scenario())


@pytest.mark.parametrize("name", AGENTS)
def test_abort_without_a_reply_does_not_fail(name: str):
    async def scenario() -> None:
        async with AGENTS[name]() as agent:
            await agent.abort()

    run(scenario())
