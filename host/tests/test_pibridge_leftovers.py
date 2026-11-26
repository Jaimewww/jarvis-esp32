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
