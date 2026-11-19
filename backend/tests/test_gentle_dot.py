"""The Gentle Dot adapter (contract C3, ``app.ports.Agent``) against a fake daemon.

No real daemon and no token file: the fake daemon in ``fake_gentle_dot`` listens on
localhost and every test passes its token explicitly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from fake_gentle_dot import TOKEN, FakeDaemon, Session, free_port

from app.agents.gentle_dot import (
    DEFAULT_TOKEN_PATH,
    DEFAULT_URL,
    GentleDotAgent,
    GentleDotBusy,
    GentleDotDaemonError,
    GentleDotError,
    GentleDotProtocolMismatch,
    GentleDotStopped,
    GentleDotTimeout,
    GentleDotTokenError,
    GentleDotUnauthorized,
    GentleDotUnavailable,
    GentleDotUnsupported,
)

T = TypeVar("T")
ASK = "abre firefox y busca el clima"


def run(coro: Awaitable[T]) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(coro, 10)

    return asyncio.run(bounded())


async def collect(agent: GentleDotAgent, text: str = ASK, turn_id: str = "t1") -> list[str]:
    return [chunk async for chunk in agent.reply(text, turn_id=turn_id)]


def frame_types(session: Session) -> list[Any]:
    return [frame.get("type") for frame in session.received]


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


# --- Happy path and streaming ---------------------------------------------------


def test_reply_sends_hello_and_text_and_yields_the_answer_by_sentence():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.emit({"type": "agent_state", "state": "thinking"})
        await s.delta("Listo. Abrí ")
        await s.delta("Firefox y ")
        await s.delta("busqué el clima.")
        await s.done("Listo. Abrí Firefox y busqué el clima.")

    async def scenario() -> tuple[list[str], FakeDaemon]:
        async with FakeDaemon(script) as daemon:
            agent = GentleDotAgent(daemon.url, token=TOKEN)
            chunks = await collect(agent)
            await asyncio.wait_for(daemon.session.closed.wait(), 2)
            return chunks, daemon

    chunks, daemon = run(scenario())
    assert chunks == ["Listo.", "Abrí Firefox y busqué el clima."]
    assert daemon.session.received == [
        {"type": "hello", "token": TOKEN, "protocol": 1},
        {"type": "send", "text": ASK},
    ]
    assert daemon.session.closed.is_set()


def test_first_sentence_arrives_while_the_daemon_is_still_working():
    first_seen = asyncio.Event()
    done_sent: list[bool] = []

    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("Empiezo con la tarea. Voy")
        await first_seen.wait()  # nothing more until the caller has the first chunk
        await s.emit(
            {
                "type": "activity",
                "messageId": "a1",
                "activity": {"id": "x", "kind": "app", "title": "Firefox", "status": "done"},
            }
        )
        await s.delta(" a tardar un poco.")
        done_sent.append(True)
        await s.done("Empiezo con la tarea. Voy a tardar un poco.")

    async def scenario() -> list[str]:
        async with FakeDaemon(script) as daemon:
            agent = GentleDotAgent(daemon.url, token=TOKEN)
            chunks: list[str] = []
            async for chunk in agent.reply(ASK, turn_id="t1"):
                if not chunks:
                    assert done_sent == []  # streamed before the daemon finished
                    first_seen.set()
                chunks.append(chunk)
            return chunks

    assert run(scenario()) == ["Empiezo con la tarea.", "Voy a tardar un poco."]


def test_reply_assembles_only_its_own_message_and_trusts_message_done():
    async def script(s: Session, text: str) -> None:
        await s.raw("not json")
        await s.emit({"type": "something_new", "field": 1})
        # The tail of an earlier answer, before our echo: not ours.
        await s.delta("Respuesta vieja. ", message_id="old")
        await s.done("Respuesta vieja.", message_id="old")
        await s.echo("otro cliente", message_id="u0")
        await s.echo(text, message_id="u1")
        await s.delta("Hecho", message_id="a1")
        # A delta was lost on the way; message_done carries the whole text.
        await s.done("Hecho. Todo en orden.", message_id="a1")

    async def scenario() -> list[str]:
        async with FakeDaemon(script) as daemon:
            return await collect(GentleDotAgent(daemon.url, token=TOKEN))

    assert run(scenario()) == ["Hecho.", "Todo en orden."]


def test_decimal_points_do_not_split_a_sentence():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("La versión 3.5 está lista! ¿Algo más?")
        await s.done("La versión 3.5 está lista! ¿Algo más?")

    async def scenario() -> list[str]:
        async with FakeDaemon(script) as daemon:
            return await collect(GentleDotAgent(daemon.url, token=TOKEN))

    assert run(scenario()) == ["La versión 3.5 está lista!", "¿Algo más?"]


# --- Errors ----------------------------------------------------------------------


def test_daemon_error_frame_raises_daemon_error():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.emit({"type": "error", "code": "agent_failed", "message": "model offline"})

    async def scenario() -> tuple[GentleDotDaemonError, FakeDaemon]:
        async with FakeDaemon(script) as daemon:
            with pytest.raises(GentleDotDaemonError) as info:
                await collect(GentleDotAgent(daemon.url, token=TOKEN))
            await asyncio.wait_for(daemon.session.closed.wait(), 2)
            return info.value, daemon

    error, daemon = run(scenario())
    assert isinstance(error, GentleDotError)
    assert error.code == "agent_failed"
    assert error.message == "model offline"
    assert "model offline" in str(error)
    # The daemon may still be working on the request: the adapter stops it.
    assert frame_types(daemon.session) == ["hello", "send", "abort"]


def test_message_done_with_error_raises_after_the_partial_answer():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("Abrí Firefox. Luego")
        await s.done("Abrí Firefox. Luego", error="tool crashed")

    async def scenario() -> tuple[list[str], GentleDotDaemonError, FakeDaemon]:
        async with FakeDaemon(script) as daemon:
            chunks: list[str] = []
            with pytest.raises(GentleDotDaemonError) as info:
                async for chunk in GentleDotAgent(daemon.url, token=TOKEN).reply(
                    ASK, turn_id="t1"
                ):
                    chunks.append(chunk)
            await asyncio.wait_for(daemon.session.closed.wait(), 2)
            return chunks, info.value, daemon

    chunks, error, daemon = run(scenario())
    assert chunks == ["Abrí Firefox."]
    assert frame_types(daemon.session) == ["hello", "send"]  # finished: nothing to abort
    assert error.code == "message_failed"
    assert error.message == "tool crashed"


def test_wrong_token_raises_unauthorized_and_sends_nothing():
    async def script(s: Session, text: str) -> None:  # pragma: no cover - never reached
        raise AssertionError("the daemon must not get a send")

    async def scenario() -> tuple[GentleDotUnauthorized, FakeDaemon]:
        async with FakeDaemon(script) as daemon:
            with pytest.raises(GentleDotUnauthorized) as info:
                await collect(GentleDotAgent(daemon.url, token="wrong"))
            return info.value, daemon

    error, daemon = run(scenario())
    assert "4401" in str(error)
    assert "wrong" not in str(error)  # the token never shows up in errors
    assert daemon.session.received == [{"type": "hello", "token": "wrong", "protocol": 1}]


def test_protocol_mismatch_close_raises_protocol_mismatch(monkeypatch):
    import app.agents.gentle_dot as gentle_dot

    monkeypatch.setattr(gentle_dot, "PROTOCOL_VERSION", 2)

    async def script(s: Session, text: str) -> None:  # pragma: no cover
        raise AssertionError("unreachable")

    async def scenario() -> GentleDotProtocolMismatch:
        async with FakeDaemon(script) as daemon:
            with pytest.raises(GentleDotProtocolMismatch) as info:
                await collect(GentleDotAgent(daemon.url, token=TOKEN))
            return info.value

    assert "4400" in str(run(scenario()))


def test_unreachable_daemon_raises_unavailable():
    url = f"ws://127.0.0.1:{free_port()}/ws"

    async def scenario() -> GentleDotUnavailable:
        with pytest.raises(GentleDotUnavailable) as info:
            await collect(GentleDotAgent(url, token=TOKEN, open_timeout=2))
        return info.value

    error = run(scenario())
    assert url in str(error)
    assert isinstance(error, GentleDotError)


def test_silent_daemon_times_out_and_is_asked_to_abort():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("Empiezo. ")
        # ... and then nothing, past the idle timeout.

    async def scenario() -> tuple[list[str], GentleDotTimeout, FakeDaemon, GentleDotAgent]:
        async with FakeDaemon(script) as daemon:
            agent = GentleDotAgent(daemon.url, token=TOKEN, idle_timeout=0.3)
            chunks: list[str] = []
            with pytest.raises(GentleDotTimeout) as info:
                async for chunk in agent.reply(ASK, turn_id="t1"):
                    chunks.append(chunk)
            await asyncio.wait_for(daemon.session.closed.wait(), 2)
            return chunks, info.value, daemon, agent

    chunks, error, daemon, agent = run(scenario())
    assert chunks == ["Empiezo."]
    assert "0.3" in str(error)
    assert frame_types(daemon.session) == ["hello", "send", "abort"]
    assert agent.busy is False


def test_missing_ready_times_out():
    async def script(s: Session, text: str) -> None:  # pragma: no cover
        raise AssertionError("unreachable")

    async def scenario() -> tuple[GentleDotTimeout, FakeDaemon]:
        async with FakeDaemon(script, send_ready=False) as daemon:
            with pytest.raises(GentleDotTimeout) as info:
                await collect(GentleDotAgent(daemon.url, token=TOKEN, open_timeout=0.3))
            return info.value, daemon

    error, daemon = run(scenario())
    assert "ready" in str(error)
    assert frame_types(daemon.session) == ["hello"]


# --- Cancellation ----------------------------------------------------------------


def test_abort_without_a_reply_in_progress_does_nothing():
    async def scenario() -> None:
        agent = GentleDotAgent(f"ws://127.0.0.1:{free_port()}/ws", token=TOKEN)
        await agent.abort()  # no daemon at all: must not connect nor fail
        await agent.abort()

    run(scenario())


def test_abort_stops_the_daemon_and_ends_the_reply():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("Abriendo Firefox. Buscando")

    async def on_abort(s: Session) -> None:
        await s.done("Abriendo Firefox. Buscando", stopped=True)

    async def scenario() -> tuple[list[str], FakeDaemon, GentleDotAgent]:
        async with FakeDaemon(script, on_abort=on_abort) as daemon:
            agent = GentleDotAgent(daemon.url, token=TOKEN)
            chunks: list[str] = []
            async for chunk in agent.reply(ASK, turn_id="t1"):
                chunks.append(chunk)
                await agent.abort()
            await asyncio.wait_for(daemon.session.closed.wait(), 2)
            return chunks, daemon, agent

    chunks, daemon, agent = run(scenario())
    assert chunks == ["Abriendo Firefox."]  # the cut-off tail is not spoken
    assert frame_types(daemon.session) == ["hello", "send", "abort"]
    assert agent.busy is False


def test_stop_by_someone_else_raises_stopped():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("Abriendo")
        await s.done("Abriendo", stopped=True)

    async def scenario() -> GentleDotStopped:
        async with FakeDaemon(script) as daemon:
            with pytest.raises(GentleDotStopped) as info:
                await collect(GentleDotAgent(daemon.url, token=TOKEN))
            return info.value

    assert isinstance(run(scenario()), GentleDotError)


def test_cancelling_the_caller_aborts_the_daemon_and_closes_the_socket():
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.delta("Trabajando. ")

    async def scenario() -> tuple[FakeDaemon, GentleDotAgent]:
        async with FakeDaemon(script) as daemon:
            agent = GentleDotAgent(daemon.url, token=TOKEN)
            got_first = asyncio.Event()

            async def consume() -> None:
                async for _ in agent.reply(ASK, turn_id="t1"):
                    got_first.set()

            task = asyncio.create_task(consume())
            await asyncio.wait_for(got_first.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(daemon.session.closed.wait(), 2)
            return daemon, agent

    daemon, agent = run(scenario())
    assert frame_types(daemon.session) == ["hello", "send", "abort"]
    assert agent.busy is False


def test_second_reply_while_one_runs_raises_busy():
    release = asyncio.Event()

    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await release.wait()
        await s.done("Hecho.")

    async def scenario() -> tuple[list[str], FakeDaemon]:
        async with FakeDaemon(script) as daemon:
            agent = GentleDotAgent(daemon.url, token=TOKEN)
            first = asyncio.create_task(collect(agent))
            await wait_for(lambda: agent.busy)
            with pytest.raises(GentleDotBusy):
                await collect(agent, "otra cosa", "t2")
            release.set()
            chunks = await first
            return chunks, daemon

    chunks, daemon = run(scenario())
    assert chunks == ["Hecho."]
    assert len(daemon.sessions) == 1  # the refused call never connected


def test_reset_is_refused_until_the_protocol_has_a_way_to_do_it():
    async def scenario() -> FakeDaemon:
        async def script(s: Session, text: str) -> None:  # pragma: no cover
            raise AssertionError("unreachable")

        async with FakeDaemon(script) as daemon:
            with pytest.raises(GentleDotUnsupported):
                await GentleDotAgent(daemon.url, token=TOKEN).reset()
            return daemon

    assert run(scenario()).sessions == []


# --- Configuration ---------------------------------------------------------------


def test_from_env_defaults_without_reading_the_token():
    agent = GentleDotAgent.from_env({})
    assert agent.url == DEFAULT_URL == "ws://127.0.0.1:4317/ws"
    assert agent.token_path == DEFAULT_TOKEN_PATH
    assert agent.idle_timeout == 120.0


def test_from_env_reads_url_timeouts_and_token_file_lazily(tmp_path: Path):
    async def script(s: Session, text: str) -> None:
        await s.echo(text)
        await s.done("Hecho.")

    token_file = tmp_path / "token"

    async def scenario() -> tuple[list[str], FakeDaemon]:
        async with FakeDaemon(script) as daemon:
            agent = GentleDotAgent.from_env(
                {
                    "JARVIS_GENTLE_DOT_URL": daemon.url,
                    "JARVIS_GENTLE_DOT_TOKEN_FILE": str(token_file),
                    "JARVIS_GENTLE_DOT_IDLE_TIMEOUT_S": "7.5",
                }
            )
            assert agent.idle_timeout == 7.5
            token_file.write_text(f"{TOKEN}\n", encoding="utf-8")  # read per reply
            return await collect(agent), daemon

    chunks, daemon = run(scenario())
    assert chunks == ["Hecho."]
    assert daemon.session.received[0]["token"] == TOKEN


def test_token_env_var_wins_over_the_file(tmp_path: Path):
    agent = GentleDotAgent.from_env(
        {"JARVIS_GENTLE_DOT_TOKEN": "from-env", "JARVIS_GENTLE_DOT_TOKEN_FILE": str(tmp_path)}
    )
    assert agent.read_token() == "from-env"


def test_missing_token_file_raises_token_error_without_connecting(tmp_path: Path):
    missing = tmp_path / "nope"
    agent = GentleDotAgent.from_env(
        {
            "JARVIS_GENTLE_DOT_URL": f"ws://127.0.0.1:{free_port()}/ws",
            "JARVIS_GENTLE_DOT_TOKEN_FILE": str(missing),
        }
    )
    with pytest.raises(GentleDotTokenError) as info:
        run(collect(agent))
    assert str(missing) in str(info.value)
    assert agent.busy is False


def test_empty_token_file_raises_token_error(tmp_path: Path):
    empty = tmp_path / "token"
    empty.write_text("  \n", encoding="utf-8")
    agent = GentleDotAgent.from_env({"JARVIS_GENTLE_DOT_TOKEN_FILE": str(empty)})
    with pytest.raises(GentleDotTokenError):
        agent.read_token()
