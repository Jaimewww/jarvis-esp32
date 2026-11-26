"""The second hardening pass of the bridge (S14 items 1-6 in ``odd/tasks/pi-bridge.md``)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fake_pi import FakeLauncher, FakePi, reply
from pibridge_harness import bridge_at, raw, run, wait_for
from websockets.asyncio.client import ClientConnection

from jarvis.pibridge.server import MAX_PI_REQUESTS
from jarvis.pibridge import turns
from jarvis.pibridge.turns import COMMAND_TIMEOUT_S, Bridge

Frames = list[dict[str, Any]]


async def frames_until(socket: ClientConnection, done: Callable[[Frames], bool], timeout: float = 2) -> Frames:
    """Every frame (without ``seq``) up to the one that makes ``done`` true."""

    seen: Frames = []
    while not done(seen):
        frame = json.loads(await asyncio.wait_for(socket.recv(), timeout))
        frame.pop("seq", None)
        seen.append(frame)
    return seen


async def quiet(socket: ClientConnection, wait: float = 0.15) -> Frames:
    """The frames that arrive within ``wait`` seconds."""

    seen: Frames = []
    with contextlib.suppress(TimeoutError):
        while True:
            seen.append(json.loads(await asyncio.wait_for(socket.recv(), wait)))
    return seen


def of(frames: Frames, kind: str) -> Frames:
    return [f for f in frames if f["type"] == kind]


def written(pi: FakePi) -> list[str]:
    return [str(r["type"]) for r in pi.written]


def running_on(pi: FakePi) -> tuple[str, str]:
    return str(pi.model["id"]), pi.thinking


# --- 1. Too many model requests of one client ---------------------------------------------


def test_a_model_request_beyond_the_cap_is_answered_with_an_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        loop = asyncio.get_running_loop()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            pi.mute.add("get_available_models")  # Pi stops answering
            async with raw(running) as socket:
                await socket.send(json.dumps({"type": "models_list"}))  # the one that runs
                await wait_for(lambda: "get_available_models" in written(pi))
                for _ in range(MAX_PI_REQUESTS + 1):  # the cap waits, one more is refused
                    await socket.send(json.dumps({"type": "models_list"}))
                start = loop.time()
                seen = await frames_until(socket, lambda s: bool(of(s, "error")))
                assert loop.time() - start < 1
                assert [f["code"] for f in of(seen, "error")] == ["busy"]
                # The read loop is free: another request is answered at once.
                await socket.send(json.dumps({"type": "get_history"}))
                history = await frames_until(socket, lambda s: bool(of(s, "history")))
                assert loop.time() - start < 1
                assert not of(history, "error") and not of(history, "models")
                assert written(pi).count("get_available_models") == 1
                start = loop.time()
        assert loop.time() - start < COMMAND_TIMEOUT_S / 10  # shutdown does not drain the queue

    run(scenario())


# --- 2. Model switches across clients -----------------------------------------------------


def model_set(provider: str, model_id: str, thinking: str | None = None) -> str:
    return json.dumps({"type": "model_set", "provider": provider, "id": model_id, "thinking": thinking})


def refusal(pi: FakePi, record: dict[str, Any]) -> bool:
    pi.emit({"id": record["id"], "type": "response", "command": record["type"], "success": False,
             "error": f"{record['type']} refused"})
    return True


def test_model_switches_of_two_clients_run_one_after_the_other(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            other_switched = asyncio.Event()

            async def interleave(_pi: FakePi, record: dict[str, Any]) -> bool:
                # The first switch pauses half way until the second one switched (or 0.3 s).
                if record["type"] == "set_model" and record.get("modelId") == "plain":
                    other_switched.set()
                if record["type"] == "get_available_thinking_levels" and not other_switched.is_set():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(other_switched.wait(), 0.3)
                return False

            pi.intercept = interleave
            async with raw(running) as first, raw(running) as second:
                await first.send(model_set("anthropic", "claude-x", "max"))  # Pi lists no "max": rolls back
                await wait_for(lambda: "get_available_thinking_levels" in written(pi))
                await second.send(model_set("openai", "plain"))
                first_seen = await frames_until(first, lambda s: bool(of(s, "error")) and bool(of(s, "models")))
                second_seen = await frames_until(second, lambda s: bool(of(s, "models")))
                assert [f["code"] for f in of(first_seen, "error")] == ["thinking_unavailable"]
                assert not of(second_seen, "error")
                assert [f["current"]["id"] for f in of(first_seen, "models")] == ["plain"]
                assert [f["current"]["id"] for f in of(second_seen, "models")] == ["plain"]
                assert running_on(pi) == ("plain", "off")
                assert running.bridge.model == "openai/plain"
                assert not await quiet(first) and not await quiet(second)

    run(scenario())


def test_a_client_that_leaves_during_its_model_set_does_not_leave_pi_half_switched(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            release = asyncio.Event()

            async def hold(_pi: FakePi, record: dict[str, Any]) -> bool:
                if record["type"] == "get_available_thinking_levels":
                    await release.wait()  # between set_model and set_thinking_level
                return False

            pi.intercept = hold
            async with raw(running) as watcher:
                async with raw(running) as chooser:
                    await chooser.send(model_set("anthropic", "claude-x", "high"))
                    await wait_for(lambda: "get_available_thinking_levels" in written(pi))
                await wait_for(lambda: running.hub.size == 1)
                release.set()
                seen = await frames_until(watcher, lambda s: bool(of(s, "models")))
                assert not of(seen, "error")
                (models,) = of(seen, "models")
                assert (models["current"]["id"], models["current"]["thinking"]) == ("claude-x", "high")
                assert running_on(pi) == ("claude-x", "high")
                assert running.bridge.model == "anthropic/claude-x"

    run(scenario())


def test_a_failed_put_back_reports_the_model_pi_really_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            pi.refused_levels.add("medium")

            async def refuse_going_back(pi: FakePi, record: dict[str, Any]) -> bool:
                if record["type"] == "set_model" and record.get("modelId") == "test-model":
                    return refusal(pi, record)
                return False

            pi.intercept = refuse_going_back
            async with raw(running) as chooser, raw(running) as watcher:
                await chooser.send(model_set("anthropic", "claude-x", "medium"))
                chooser_seen = await frames_until(chooser, lambda s: bool(of(s, "error")) and bool(of(s, "models")))
                watcher_seen = await frames_until(watcher, lambda s: bool(of(s, "models")))
                assert [f["code"] for f in of(chooser_seen, "error")] == ["thinking_unavailable"]
                assert not of(watcher_seen, "error")
                for frames in (chooser_seen, watcher_seen):
                    (models,) = of(frames, "models")
                    assert (models["current"]["id"], models["current"]["thinking"]) == ("claude-x", "low")
                assert running_on(pi) == ("claude-x", "low")
                assert running.bridge.model == "anthropic/claude-x"
                assert not await quiet(chooser) and not await quiet(watcher)
            async with raw(running) as late:
                await late.send(json.dumps({"type": "auth_list"}))
                (accounts,) = of(await frames_until(late, lambda s: bool(of(s, "auth_providers"))), "auth_providers")
                assert [p["id"] for p in accounts["providers"]] == ["anthropic"]

    run(scenario())


def test_a_models_failure_after_the_switch_puts_pi_back(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            listings = 0

            async def refuse_second_listing(pi: FakePi, record: dict[str, Any]) -> bool:
                nonlocal listings
                if record["type"] == "get_available_models":
                    listings += 1
                    if listings == 2:  # the models frame after the switch
                        return refusal(pi, record)
                return False

            pi.intercept = refuse_second_listing
            async with raw(running) as chooser, raw(running) as watcher:
                await chooser.send(model_set("openai", "plain"))
                chooser_seen = await frames_until(chooser, lambda s: bool(of(s, "error")))
                assert [f["code"] for f in of(chooser_seen, "error")] == ["agent_unavailable"]
                assert not of(chooser_seen, "models")
                assert not await quiet(chooser) and not await quiet(watcher)
                assert running_on(pi) == ("test-model", "low")
                assert running.bridge.model == "openai/test-model"
                assert written(pi).count("set_model") == 2  # the switch and the put-back

    run(scenario())


# --- 3. A send right after a model_set ----------------------------------------------------


def test_a_send_right_after_a_model_set_runs_on_the_new_model(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        async with bridge_at(launcher, tmp_path / "token") as running, raw(running) as socket:
            await socket.send(model_set("anthropic", "claude-x", "high"))
            await socket.send(json.dumps({"type": "send", "text": "hola"}))
            seen = await frames_until(socket, lambda s: bool(of(s, "message_done")))
            kinds = [f["type"] for f in seen]
            assert "error" not in kinds
            assert kinds.index("models") < kinds.index("user_message")
            assert [f["text"] for f in of(seen, "message_done")] == ["ok"]
            pi = launcher.current
            sent = written(pi)
            assert sent.index("set_model") < sent.index("set_thinking_level") < sent.index("prompt")
            assert running_on(pi) == ("claude-x", "high")
            assert len(launcher.launches) == 1

    run(scenario())


# --- 4. An unexpected failure in the model-request worker ---------------------------------


def test_an_unexpected_failure_of_a_model_request_is_answered(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()

        async def malformed(pi: FakePi, record: dict[str, Any]) -> bool:
            if record["type"] == "get_available_models":
                pi.respond(record, "get_available_models", data={"models": 5})  # not a list
                return True
            return False

        async def install(pi: FakePi) -> None:
            pi.intercept = malformed

        launcher.on_launch = install
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with raw(running) as asker, raw(running) as other:
                await asker.send(json.dumps({"type": "models_list"}))
                seen = await frames_until(asker, lambda s: bool(of(s, "error")))
                assert [f["code"] for f in of(seen, "error")] == ["internal_error"]
                assert not of(seen, "models")
                assert not await quiet(other)
                launcher.current.intercept = None
                await asker.send(json.dumps({"type": "models_list"}))  # the worker still serves
                (models,) = of(await frames_until(asker, lambda s: bool(of(s, "models"))), "models")
                assert models["current"]["id"] == "test-model"

    run(scenario())


# --- 5. The watchdog and a slow launch ----------------------------------------------------


def test_a_launch_slower_than_the_record_timeout_is_not_a_stuck_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("hola"))

        async def slow(_pi: FakePi) -> None:
            await asyncio.sleep(0.5)

        launcher.on_launch = slow
        async with bridge_at(launcher, tmp_path / "token", record_timeout=0.2) as running, raw(running) as socket:
            await socket.send(json.dumps({"type": "send", "text": "hola"}))
            seen = await frames_until(socket, lambda s: {"type": "agent_state", "state": "idle"} in s, timeout=3)
            assert not of(seen, "error")
            assert [f["text"] for f in of(seen, "user_message")] == ["hola"]
            assert [f["text"] for f in of(seen, "message_done")] == ["hola"]
            assert len(launcher.launches) == 1
            assert written(launcher.current).count("prompt") == 1

    run(scenario())


# --- 6. close() during a launch -----------------------------------------------------------


def test_close_stops_a_pi_whose_launch_was_running() -> None:
    async def scenario() -> None:
        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher(reply("hola"))

        async def slow(_pi: FakePi) -> None:
            await asyncio.sleep(0.3)

        launcher.on_launch = slow
        bridge = Bridge(launcher, emit, model="openai/m")
        sending = asyncio.ensure_future(bridge.send("hola"))
        await asyncio.sleep(0.05)  # the launch is running
        loop = asyncio.get_running_loop()
        start = loop.time()
        await bridge.close()
        assert loop.time() - start < 1
        await asyncio.wait_for(sending, 1)
        await asyncio.sleep(0.1)
        (pi,) = launcher.launches
        assert pi.closed and pi.exit_code is not None
        assert "prompt" not in written(pi)
        assert not [f for f in frames if f["type"] in ("user_message", "message_delta", "message_done")]

    run(scenario())


# --- S18.1 Shutdown during a model_set ----------------------------------------------------


def test_shutdown_during_model_sets_launches_no_pi_and_leaves_no_switch_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        loop = asyncio.get_running_loop()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            pi.mute.add("set_thinking_level")  # the first switch waits on Pi
            async with contextlib.AsyncExitStack() as clients:
                first = await clients.enter_async_context(raw(running))
                second = await clients.enter_async_context(raw(running))
                await first.send(model_set("anthropic", "claude-x", "medium"))
                await wait_for(lambda: "set_thinking_level" in written(pi))
                await second.send(model_set("openai", "plain"))  # waits for the first
                await asyncio.sleep(0.05)
                start = loop.time()
                await running.server.close()  # the order of __main__.serve
                await running.bridge.close()
                assert loop.time() - start < 1
                switches = [t for t in asyncio.all_tasks() if not t.done()
                            and "switch" in getattr(t.get_coro(), "__qualname__", "")]
                assert not switches
                sent = len(pi.written)
                await asyncio.sleep(0.3)
                assert len(launcher.launches) == 1
                assert pi.closed and pi.exit_code is not None
                assert len(pi.written) == sent  # nothing more went to Pi after close()
                assert running.bridge.model == "openai/test-model"

    run(scenario())


# --- S18.2 abort right after model_set and send -------------------------------------------


def test_abort_drops_a_send_still_waiting_behind_its_model_set(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            release = asyncio.Event()

            async def hold(_pi: FakePi, record: dict[str, Any]) -> bool:
                if record["type"] == "set_model":
                    await release.wait()
                return False

            pi.intercept = hold
            async with raw(running) as socket, raw(running) as other:
                await socket.send(model_set("anthropic", "claude-x"))
                await socket.send(json.dumps({"type": "send", "text": "hola"}))
                await socket.send(json.dumps({"type": "abort"}))
                await wait_for(lambda: "set_model" in written(pi))
                await asyncio.sleep(0.1)
                release.set()
                seen = await frames_until(socket, lambda s: bool(of(s, "models")))
                seen += await quiet(socket)
                assert [f["type"] for f in seen] == ["models"]
                assert [f["type"] for f in await quiet(other)] == ["models"]
                assert "prompt" not in written(pi)
                assert running.bridge.agent_state == "idle"
                # The next send works.
                await socket.send(json.dumps({"type": "send", "text": "otra"}))
                turn = await frames_until(socket, lambda s: {"type": "agent_state", "state": "idle"} in s)
                assert [f["text"] for f in of(turn, "user_message")] == ["otra"]
                assert [f["text"] for f in of(turn, "message_done")] == ["ok"]
                assert [r["message"] for r in pi.written if r["type"] == "prompt"] == ["otra"]
                assert len(launcher.launches) == 1

    run(scenario())


# --- S18.3 A failed switch on a silent Pi -------------------------------------------------


@pytest.mark.parametrize("thinking", [None, "high"])
def test_a_failed_switch_on_a_silent_pi_is_answered_within_one_command_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thinking: str | None
) -> None:
    timeout = 0.3
    monkeypatch.setattr(turns, "COMMAND_TIMEOUT_S", timeout)

    async def scenario() -> None:
        launcher = FakeLauncher()
        loop = asyncio.get_running_loop()
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
            async with raw(running) as first, raw(running) as second:
                start = loop.time()
                await first.send(model_set("anthropic", "claude-x", thinking))
                await wait_for(lambda: "set_model" in written(pi))
                await second.send(model_set("openai", "plain"))  # waits for the lock
                first_seen = await frames_until(first, lambda s: bool(of(s, "error")))
                first_at = loop.time() - start
                second_seen = await frames_until(second, lambda s: bool(of(s, "error")))
                second_at = loop.time() - start
                assert [f["code"] for f in of(first_seen, "error")] == ["agent_unavailable"]
                assert [f["code"] for f in of(second_seen, "error")] == ["agent_unavailable"]
                assert timeout <= first_at < timeout * 1.7
                assert second_at < timeout * 2.7  # the lock was held for one timeout only
                assert not of(first_seen, "models") and not of(second_seen, "models")
                assert written(pi).count("set_model") == 1  # no put-back to a silent Pi
                assert len(launcher.launches) == 1

    run(scenario())


# --- S18.4 abort during a launch ----------------------------------------------------------


def test_abort_during_a_launch_ends_the_turn_and_the_next_send_works(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        launched = asyncio.Event()

        async def gated(_pi: FakePi) -> None:
            await launched.wait()

        launcher.on_launch = gated
        loop = asyncio.get_running_loop()
        async with bridge_at(launcher, tmp_path / "token") as running, \
                raw(running) as sender, raw(running) as stopper:
            # The sender's read loop waits for the launch its send started; the abort comes from
            # the other client (the TUI and the backend share the bridge).
            await sender.send(json.dumps({"type": "send", "text": "hola"}))
            for socket in (sender, stopper):
                assert await frames_until(socket, lambda s: bool(of(s, "agent_state"))) == [
                    {"type": "agent_state", "state": "working"}]
            start = loop.time()
            await stopper.send(json.dumps({"type": "abort"}))
            for socket in (sender, stopper):
                seen = await frames_until(socket, lambda s: bool(of(s, "agent_state")))
                assert seen == [{"type": "agent_state", "state": "idle"}]
            assert loop.time() - start < 0.5  # the launch has not finished
            assert not launcher.launches and running.bridge.agent_state == "idle"
            launched.set()
            await wait_for(lambda: bool(launcher.launches))
            assert not await quiet(sender) and not await quiet(stopper)
            assert "prompt" not in written(launcher.current)
            await sender.send(json.dumps({"type": "send", "text": "otra"}))
            turn = await frames_until(sender, lambda s: {"type": "agent_state", "state": "idle"} in s)
            assert not of(turn, "error")
            assert [f["text"] for f in of(turn, "user_message")] == ["otra"]
            assert [f["text"] for f in of(turn, "message_done")] == ["ok"]
            assert [r["message"] for r in launcher.current.written if r["type"] == "prompt"] == ["otra"]
            assert len(launcher.launches) == 1 and not launcher.current.closed

    run(scenario())


# --- S18.5 A send refused for a full queue ------------------------------------------------


def test_a_send_refused_for_a_full_request_queue_gets_queue_full(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            pi = launcher.current
            pi.mute.add("get_available_models")  # the model_set waits on Pi
            async with raw(running) as socket:
                await socket.send(model_set("openai", "plain"))
                await wait_for(lambda: "get_available_models" in written(pi))
                for _ in range(MAX_PI_REQUESTS):
                    await socket.send(json.dumps({"type": "models_list"}))
                await socket.send(json.dumps({"type": "send", "text": "hola"}))
                seen = await frames_until(socket, lambda s: bool(of(s, "error")))
                seen += await quiet(socket)
                assert [f["code"] for f in of(seen, "error")] == ["queue_full"]
                assert [f["type"] for f in seen] == ["error"]
                assert written(pi).count("get_available_models") == 1 and "prompt" not in written(pi)

    run(scenario())
