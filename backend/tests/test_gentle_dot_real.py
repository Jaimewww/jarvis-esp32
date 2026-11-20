"""The Gentle Dot adapter (contract C3) against the REAL daemon. Opt-in.

Skipped unless ``JARVIS_REAL_DAEMON=1``, so CI (which has no daemon) stays green.
Run on a machine where the daemon is up (for example the ``gentle-dot.service`` user
service from ``host/systemd/``)::

    cd backend
    JARVIS_REAL_DAEMON=1 python -m pytest tests/test_gentle_dot_real.py -v -rs

Configuration comes from ``GentleDotAgent.from_env()`` (``JARVIS_GENTLE_DOT_URL``,
``JARVIS_GENTLE_DOT_TOKEN_FILE``…); the token is read from its file by the adapter and
never printed. With an AI account connected in Gentle Dot, the tests send two short
prompts to the assistant (they appear in its active conversation). Without one, the
answer tests are skipped with the daemon's own message and the "not signed in" test
checks that message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import pytest
from websockets.asyncio.client import connect

from app.agents.gentle_dot import (
    GentleDotAgent,
    GentleDotNotSignedIn,
    GentleDotUnauthorized,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("JARVIS_REAL_DAEMON") != "1",
    reason="real Gentle Dot daemon tests are opt-in: set JARVIS_REAL_DAEMON=1",
)

T = TypeVar("T")
#: What upstream f97d56d answers a ``send`` with when no AI account is connected.
NO_ACCOUNT = "Connect an AI account first: open Accounts or type /login."
SHORT = "Jarvis test: answer with one short sentence that says OK."
LONG = (
    "Jarvis test: count from 1 to 300, writing each number as a separate full sentence "
    "in words, for example 'One. Two. Three.'"
)


def run(coro: Awaitable[T], timeout: float = 120) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(bounded())


def agent() -> GentleDotAgent:
    return GentleDotAgent.from_env()


@asynccontextmanager
async def observer() -> AsyncIterator[list[dict[str, Any]]]:
    """A second, read-only client: the daemon broadcasts the conversation to every client."""

    probe = agent()
    frames: list[dict[str, Any]] = []
    async with connect(probe.url) as socket:
        await socket.send(json.dumps({"type": "hello", "token": probe.read_token(), "protocol": 1}))
        while json.loads(await asyncio.wait_for(socket.recv(), 5)).get("type") != "ready":
            pass

        async def read() -> None:
            async for raw in socket:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    frames.append(payload)

        task = asyncio.create_task(read())
        try:
            yield frames
        finally:
            await asyncio.sleep(0.5)  # let the last broadcasts arrive
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _short_reply() -> dict[str, Any]:
    async with observer() as frames:
        try:
            chunks = [c async for c in agent().reply(SHORT, turn_id="real-short")]
        except GentleDotNotSignedIn as error:
            return {"signed_in": False, "error": error}
    return {"signed_in": True, "chunks": chunks, "frames": list(frames)}


@pytest.fixture(scope="module")
def short_reply() -> dict[str, Any]:
    """One short prompt, shared by the tests that need it (each costs a model call)."""

    return run(_short_reply())


def require_account(result: dict[str, Any]) -> None:
    if not result["signed_in"]:
        pytest.skip(f"Gentle Dot has no AI account connected; the daemon says: {result['error']}")


# --- Without an account too ----------------------------------------------------------


def test_real_wrong_token_is_refused():
    async def scenario() -> None:
        wrong = GentleDotAgent(agent().url, token="jarvis-test-wrong-token")
        with pytest.raises(GentleDotUnauthorized):
            [c async for c in wrong.reply(SHORT, turn_id="real-wrong")]

    run(scenario(), 20)


def test_real_abort_without_a_reply_does_nothing():
    run(agent().abort(), 5)


def test_real_not_signed_in_surfaces_the_daemons_exact_message(short_reply: dict[str, Any]):
    if short_reply["signed_in"]:
        pytest.skip("Gentle Dot has an AI account connected; nothing to check")
    error = short_reply["error"]
    assert error.code == "no_account"
    assert str(error) == error.message == NO_ACCOUNT


# --- With an account -----------------------------------------------------------------


def test_real_reply_echoes_the_text_and_answers_in_a_separate_message(short_reply: dict[str, Any]):
    require_account(short_reply)
    chunks: list[str] = short_reply["chunks"]
    frames: list[dict[str, Any]] = short_reply["frames"]
    assert chunks and all(isinstance(c, str) and c for c in chunks)

    echoes = [f for f in frames if f.get("type") == "user_message" and f.get("text", "").strip() == SHORT]
    assert len(echoes) == 1, "the daemon echoes the sent text unchanged"
    user_id = echoes[0]["messageId"]
    after = frames[frames.index(echoes[0]) + 1 :]
    done = next(f for f in after if f.get("type") == "message_done")
    assert done["messageId"] != user_id, "the answer has its own message id"
    assert not done.get("stopped") and not done.get("error")
    deltas = [f for f in after if f.get("type") == "message_delta" and f["messageId"] == done["messageId"]]
    assert deltas, "the answer is streamed"
    assert " ".join(chunks).split() == done["text"].split()


def test_real_abort_stops_the_answer_and_the_daemon_reports_stopped(short_reply: dict[str, Any]):
    require_account(short_reply)

    async def scenario() -> tuple[list[str], list[dict[str, Any]]]:
        async with observer() as frames:
            gentle = agent()
            chunks: list[str] = []
            async for chunk in gentle.reply(LONG, turn_id="real-abort"):
                chunks.append(chunk)
                if len(chunks) == 1:
                    await gentle.abort()
            # The reply ended cleanly once the daemon confirmed the stop.
            assert not gentle.busy
        return chunks, list(frames)

    chunks, frames = run(scenario())
    assert chunks
    echo = next(f for f in frames if f.get("type") == "user_message" and f.get("text", "").strip() == LONG)
    after = frames[frames.index(echo) + 1 :]
    done = next(f for f in after if f.get("type") == "message_done")
    assert done.get("stopped") is True
