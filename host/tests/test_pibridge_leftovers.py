"""Leftovers of the T11 verify and review (S19 in ``odd/tasks/pi-bridge.md``)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import pytest
from fake_pi import FakeLauncher, FakePi, hang, reply
from pibridge_harness import bridge_at, raw, run, wait_for
from test_pibridge_hardening import Frames, frames_until, model_set, of, quiet, running_on, written
from websockets.asyncio.client import connect

from jarvis.pibridge import __main__ as bridge_main
from jarvis.pibridge import turns
from jarvis.pibridge.config import parse_config
from jarvis.pibridge.models import ModelError
from jarvis.pibridge.server import BridgeServer
from jarvis.pibridge.turns import Bridge

IDLE = {"type": "agent_state", "state": "idle"}
WORKING = {"type": "agent_state", "state": "working"}


# --- S19.1 A switch Pi accepted and then went silent --------------------------------------


@pytest.mark.parametrize("thinking", [None, "high"])
def test_a_switch_pi_accepted_before_going_silent_is_the_model_the_clients_are_told(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thinking: str | None
) -> None:
    monkeypatch.setattr(turns, "COMMAND_TIMEOUT_S", 0.2)

    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            switched = False

            async def silent_after_switch(_pi: FakePi, record: dict[str, Any]) -> bool:
                nonlocal switched
                if switched:
                    return True  # never answered
                switched = record["type"] == "set_model"
                return False

            pi.intercept = silent_after_switch
            async with raw(running) as chooser:
                await chooser.send(model_set("anthropic", "claude-x", thinking))
                seen = await frames_until(chooser, lambda s: bool(of(s, "error")))
                assert [f["code"] for f in of(seen, "error")] == ["agent_unavailable"]
            assert running_on(pi)[0] == "claude-x"  # Pi did switch
            async with raw(running) as late:  # ready is read by raw(): ask the account instead
                await late.send(json.dumps({"type": "auth_list"}))
                (accounts,) = of(await frames_until(late, lambda s: bool(of(s, "auth_providers"))), "auth_providers")
                assert [p["id"] for p in accounts["providers"]] == ["anthropic"]
            async with connect(running.url) as socket:
                await socket.send(json.dumps({"type": "hello", "token": running.token, "protocol": 1}))
                assert json.loads(await socket.recv())["model"] == "anthropic/claude-x"
            # A relaunched Pi runs the same model the frames name.
            pi.exit(1)
            await running.bridge.start()
            assert len(launcher.launches) == 2
            assert running_on(launcher.current)[0] == "claude-x"
            assert running.bridge.model == "anthropic/claude-x"

    run(scenario())


# --- S19.2 A launch that fails after an abort ---------------------------------------------


def failing_first_launch(launcher: FakeLauncher) -> asyncio.Event:
    """The first launch waits for the returned event, then fails; later ones work."""

    release = asyncio.Event()
    launches = 0

    async def on_launch(_pi: FakePi) -> None:
        nonlocal launches
        launches += 1
        if launches == 1:
            await release.wait()
            raise OSError("pi: not found")

    launcher.on_launch = on_launch
    return release


def test_a_launch_that_fails_after_its_turn_was_aborted_reports_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        release = failing_first_launch(launcher)
        async with bridge_at(launcher, tmp_path / "token") as running, \
                raw(running) as sender, raw(running) as stopper:
            await sender.send(json.dumps({"type": "send", "text": "hola"}))
            assert await frames_until(stopper, lambda s: bool(of(s, "agent_state"))) == [WORKING]
            await stopper.send(json.dumps({"type": "abort"}))
            assert await frames_until(stopper, lambda s: bool(of(s, "agent_state"))) == [IDLE]
            release.set()
            await asyncio.sleep(0.1)
            assert not await quiet(stopper)
            assert not of(await quiet(sender), "error")
            assert running.bridge.agent_state == "idle"
            # The next send launches Pi again and works.
            await stopper.send(json.dumps({"type": "send", "text": "otra"}))
            turn = await frames_until(stopper, lambda s: IDLE in s)
            assert not of(turn, "error")
            assert [f["text"] for f in of(turn, "message_done")] == ["ok"]

    run(scenario())


def test_a_send_during_an_aborted_launch_is_not_ended_by_that_launch_failing(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        release = failing_first_launch(launcher)
        async with bridge_at(launcher, tmp_path / "token") as running, \
                raw(running) as sender, raw(running) as other:
            await sender.send(json.dumps({"type": "send", "text": "hola"}))
            assert await frames_until(other, lambda s: bool(of(s, "agent_state"))) == [WORKING]
            await other.send(json.dumps({"type": "abort"}))
            assert await frames_until(other, lambda s: bool(of(s, "agent_state"))) == [IDLE]
            await other.send(json.dumps({"type": "send", "text": "otra"}))  # waits for the launch
            assert await frames_until(other, lambda s: bool(of(s, "agent_state"))) == [WORKING]
            release.set()
            turn = await frames_until(other, lambda s: IDLE in s)
            assert not of(turn, "error")
            assert [f["text"] for f in of(turn, "user_message")] == ["otra"]
            assert [f["text"] for f in of(turn, "message_done")] == ["ok"]
            assert [r["message"] for r in launcher.current.written if r["type"] == "prompt"] == ["otra"]

    run(scenario())


# --- S19.4 Nothing launches Pi after close() ----------------------------------------------


def test_nothing_launches_pi_after_close() -> None:
    async def scenario() -> None:
        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher(reply("ok"))
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.start()
        await bridge.close()
        with pytest.raises(ModelError):
            await bridge.set_model("anthropic", "claude-x", None)
        await bridge.send("hola")
        await asyncio.sleep(0.1)
        assert len(launcher.launches) == 1
        assert not [f for f in frames if f["type"] == "user_message"]

    run(scenario())


# --- S19.5 abort of the client whose send is launching Pi ---------------------------------


def test_the_senders_own_abort_during_a_launch_is_read_at_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        launched = asyncio.Event()

        async def gated(_pi: FakePi) -> None:
            await launched.wait()

        launcher.on_launch = gated
        async with bridge_at(launcher, tmp_path / "token") as running, raw(running) as sender:
            await sender.send(json.dumps({"type": "send", "text": "hola"}))
            await sender.send(json.dumps({"type": "abort"}))
            seen = await frames_until(sender, lambda s: IDLE in s, timeout=0.5)
            assert seen == [WORKING, IDLE]
            assert not launcher.launches  # the launch has not finished
            launched.set()
            await wait_for(lambda: bool(launcher.launches))
            assert not await quiet(sender)
            assert "prompt" not in written(launcher.current)

    run(scenario())


# --- S19.6 The shutdown order of __main__ -------------------------------------------------


def test_a_pi_that_exits_during_shutdown_gets_no_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = FakeLauncher(hang("uno"), reply("dos"))
    monkeypatch.setattr(bridge_main, "subprocess_launcher", lambda _settings: launcher)
    ports: list[int] = []
    start, close = BridgeServer.start, BridgeServer.close

    async def recording_start(self: BridgeServer, host: str, port: int) -> int:
        ports.append(await start(self, host, port))
        return ports[-1]

    async def exiting_close(self: BridgeServer) -> None:
        launcher.launches[0].exit(1)  # the stop signal reaches Pi too (systemd signals the whole unit)
        await asyncio.sleep(0.1)
        await close(self)

    monkeypatch.setattr(BridgeServer, "start", recording_start)
    monkeypatch.setattr(BridgeServer, "close", exiting_close)
    token_path = tmp_path / "token"
    config = parse_config(["--token-file", str(token_path), "--port", "0"])

    async def scenario() -> None:
        serving = asyncio.ensure_future(bridge_main.serve(config))
        await wait_for(lambda: bool(ports) and bool(launcher.launches))
        async with connect(f"ws://127.0.0.1:{ports[0]}/ws") as socket:
            await socket.send(json.dumps({"type": "hello", "token": token_path.read_text().strip(), "protocol": 1}))
            assert json.loads(await socket.recv())["type"] == "ready"
            await socket.send(json.dumps({"type": "send", "text": "uno"}))
            await wait_for(lambda: "prompt" in written(launcher.launches[0]))
            await socket.send(json.dumps({"type": "send", "text": "dos"}))  # queued behind "uno"
            await frames_until(socket, lambda s: bool(of(s, "queue")))
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(serving, 5)
        for pi in launcher.launches[1:]:
            assert "prompt" not in written(pi)
        assert all(pi.exit_code is not None for pi in launcher.launches)

    run(scenario())


# --- Verify notes on c7ffff9 ---------------------------------------------------------------


def test_a_send_during_close_does_not_prompt_pi_and_ends_idle() -> None:
    async def scenario() -> None:
        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher(reply("ok"), reply("ok"))
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.start()
        pi = launcher.current
        pi.mute.add("get_state")  # the switch waits on Pi: close() waits for it to be cancelled
        switching = asyncio.ensure_future(bridge.set_model("anthropic", "claude-x", None))
        await wait_for(lambda: "get_state" in written(pi))
        closing = asyncio.ensure_future(bridge.close())
        await asyncio.sleep(0)  # close() has started
        await bridge.send("during close")
        await asyncio.wait_for(closing, 1)
        await asyncio.sleep(0.05)
        during = [f for f in frames if f["type"] in ("agent_state", "error")]
        frames.clear()
        await bridge.send("after close")
        await asyncio.sleep(0.05)
        after = [f for f in frames if f["type"] in ("agent_state", "error")]
        assert "prompt" not in written(pi)
        assert len(launcher.launches) == 1
        assert during == after
        assert [f["type"] for f in during] == ["agent_state", "error", "agent_state"]
        assert during[1]["code"] == "agent_unavailable" and "shutting down" in during[1]["message"]
        assert during[-1] == IDLE and bridge.agent_state == "idle"
        assert switching.done()

    run(scenario())


def test_an_unexpected_failure_writing_the_prompt_ends_the_turn(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher()

        async def broken_prompt(pi: FakePi) -> None:
            write = pi.write

            async def failing(record: Any) -> None:
                if record.get("type") == "prompt":
                    raise RuntimeError("transport broke")
                await write(record)

            pi.write = failing  # type: ignore[method-assign]

        launcher.on_launch = broken_prompt
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.send("hola")
        await wait_for(lambda: IDLE in frames, timeout=1)
        errors = [f for f in frames if f["type"] == "error"]
        assert [f["code"] for f in errors] == ["agent_unavailable"]
        assert "transport broke" in errors[0]["message"]
        assert frames[0] == WORKING and frames[-1] == IDLE
        assert "transport broke" in caplog.text
        await bridge.close()

    run(scenario())


def test_a_send_whose_launch_runs_after_close_began_does_not_prompt_pi() -> None:
    async def scenario() -> None:
        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher(reply("ok"))
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.start()
        pi = launcher.current
        pi.mute.add("get_state")
        switching = asyncio.ensure_future(bridge.set_model("anthropic", "claude-x", None))
        await wait_for(lambda: "get_state" in written(pi))
        await bridge.send("hola")  # its launch task has not run yet
        await bridge.close()  # flags itself closed before that task runs
        await asyncio.sleep(0.05)
        assert "prompt" not in written(pi)
        assert len(launcher.launches) == 1 and switching.done()
        states = [f for f in frames if f["type"] == "agent_state"]
        assert states == [WORKING, IDLE]
        assert [f["code"] for f in frames if f["type"] == "error"] == ["agent_unavailable"]

    run(scenario())


# --- Review findings on ad26857 ------------------------------------------------------------


def test_close_ends_while_a_prompt_write_is_blocked_on_a_pi_that_stopped_reading() -> None:
    async def scenario() -> None:
        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher()
        writing = asyncio.Event()

        async def stuck_stdin(pi: FakePi) -> None:
            write, close = pi.write, pi.close
            stopped = asyncio.Event()

            async def blocking(record: Any) -> None:
                if record.get("type") == "prompt":
                    writing.set()
                    await stopped.wait()  # the pipe is full until Pi is stopped
                    raise BrokenPipeError("pi stopped")
                await write(record)

            async def closing() -> None:
                stopped.set()
                await close()

            pi.write = blocking  # type: ignore[method-assign]
            pi.close = closing  # type: ignore[method-assign]

        launcher.on_launch = stuck_stdin
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.send("a long prompt")
        await asyncio.wait_for(writing.wait(), 1)
        await asyncio.wait_for(bridge.close(), 1)
        (pi,) = launcher.launches
        assert pi.closed and pi.exit_code is not None

    run(scenario())


def test_close_stops_pi_even_when_a_launch_task_was_cancelled() -> None:
    async def scenario() -> None:
        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher(reply("ok"))
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.start()
        await bridge.send("hola")
        (prompting,) = [t for t in asyncio.all_tasks()
                        if getattr(t.get_coro(), "__qualname__", "") == "Bridge._prompt"]
        prompting.cancel()
        await asyncio.wait_for(bridge.close(), 1)
        (pi,) = launcher.launches
        assert pi.closed and pi.exit_code is not None
        assert "prompt" not in written(pi)

    run(scenario())
