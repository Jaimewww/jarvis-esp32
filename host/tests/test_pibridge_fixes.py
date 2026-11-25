"""S13: the findings of the T2 verify and review, one test (or more) per item."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fake_pi import FakePi, FakeLauncher, Behavior, hang, reply, silent
from pibridge_harness import bridge_at, jarvis, raw, reply_to, run, wait_for

from jarvis.client import AskEvent, AskResolvedEvent, ErrorEvent, ModelRef, ModelsEvent, Ready
from jarvis.pibridge import __main__ as bridge_main
from jarvis.pibridge.hub import Hub
from jarvis.pibridge.models import ModelError
from jarvis.pibridge.token import TokenError, load_or_create_token
from jarvis.pibridge.turns import COMMAND_TIMEOUT_S, Bridge

Frames = list[dict[str, Any]]


def recorder() -> tuple[Frames, Any]:
    frames: Frames = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    return frames, emit


def idle(frames: Frames) -> int:
    return frames.count({"type": "agent_state", "state": "idle"})


def dones(frames: Frames) -> list[str]:
    return [f["text"] for f in frames if f["type"] == "message_done"]


# --- 1. A question Pi closed by itself ---------------------------------------------------


def asks_and_moves_on(final: str = "Sin respuesta.") -> Behavior:
    """Ask a confirm, then end the run without the answer (the dialog timed out in Pi)."""

    async def behavior(pi: FakePi, record: dict[str, Any]) -> None:
        pi.respond(record, "prompt", data={"disposition": "started"})
        pi.emit({"type": "agent_start"})
        pi.emit({"type": "extension_ui_request", "id": "ui-1", "method": "confirm", "title": "Run it?"})
        await asyncio.sleep(0.05)
        pi.emit({"type": "message_start", "message": {"role": "assistant", "content": [], "stopReason": "pending"}})
        pi.emit({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": final}],
                                                     "stopReason": "stop"}})
        pi.emit({"type": "agent_end", "messages": []})
        pi.emit({"type": "agent_settled", "aborted": False})

    return behavior


def test_a_question_still_pending_when_the_turn_ends_is_resolved_and_forgotten() -> None:
    async def scenario() -> None:
        frames, emit = recorder()
        launcher = FakeLauncher(asks_and_moves_on(), silent("..."))
        bridge = Bridge(launcher, emit, model="openai/m", record_timeout=0.3)
        await bridge.clients_changed(1)
        await bridge.send("hazlo")
        await wait_for(lambda: idle(frames) == 1)
        assert {"type": "ask_resolved", "requestId": "ui-1"} in frames
        assert frames.index({"type": "ask_resolved", "requestId": "ui-1"}) < len(frames) - 1
        # A late answer is not forwarded to Pi.
        await bridge.answer("ui-1", {"confirmed": True})
        pi = launcher.current
        assert not [r for r in pi.written if r["type"] == "extension_ui_response"]
        # The next turn is not "waiting for the user": a stuck one still times out.
        await bridge.send("otra")
        await wait_for(lambda: idle(frames) == 2, timeout=2)
        assert [f["code"] for f in frames if f["type"] == "error"] == ["agent_timeout"]
        await bridge.close()

    run(scenario())


def test_every_client_sees_the_stale_question_resolved(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(asks_and_moves_on())
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as first, jarvis(running) as second:
                await wait_for(lambda: running.hub.size == 2)
                assert first.client.send("hazlo")
                for probe in (first, second):
                    await wait_for(lambda p=probe: bool(p.of(AskResolvedEvent)))  # type: ignore[misc]
                    assert [e.ask.request_id for e in probe.of(AskEvent)] == ["ui-1"]
                    assert [e.request_id for e in probe.of(AskResolvedEvent)] == ["ui-1"]

    run(scenario())


# --- 2. The old Pi's reader after a timeout ----------------------------------------------


def test_a_pi_given_up_on_never_reaches_the_next_turn() -> None:
    async def scenario() -> None:
        frames, emit = recorder()
        launcher = FakeLauncher(silent("Abriendo"), reply("NEW"))

        async def slow_launch(_new: FakePi) -> None:
            if launcher.launches:  # the restart: the old Pi wakes up meanwhile
                old = launcher.launches[0]
                old.emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
                old.emit({"type": "message_update",
                          "assistantMessageEvent": {"type": "text_delta", "delta": "OLD"}})
                old.emit({"type": "message_end", "message": {"role": "assistant",
                                                             "content": [{"type": "text", "text": "OLD"}],
                                                             "stopReason": "stop"}})
                old.emit({"type": "agent_settled", "aborted": False})
                await asyncio.sleep(0.2)

        launcher.on_launch = slow_launch
        bridge = Bridge(launcher, emit, model="openai/m", record_timeout=0.3)
        await bridge.send("abre firefox")
        await bridge.send("otra vez")  # queued behind the stuck turn
        await wait_for(lambda: idle(frames) == 1)
        assert len(launcher.launches) == 2
        assert "OLD" not in json.dumps(frames)
        assert dones(frames)[-1] == "NEW"
        assert [f["code"] for f in frames if f["type"] == "error"] == ["agent_timeout"]
        await bridge.close()

    run(scenario())


def test_records_of_a_replaced_pi_are_dropped() -> None:
    async def scenario() -> None:
        frames, emit = recorder()
        launcher = FakeLauncher(reply("uno"), reply("dos"))
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.send("a")
        await wait_for(lambda: idle(frames) == 1)
        old = launcher.current
        old.exit_code = 1  # exited, its reader has not seen the end yet
        await bridge.send("b")
        old._out.put_nowait({"type": "agent_settled", "aborted": False})  # a late record
        await wait_for(lambda: idle(frames) == 2)
        assert dones(frames) == ["uno", "dos"]
        await bridge.close()

    run(scenario())


# --- 3. The model after a restart ---------------------------------------------------------


async def _choose_claude_then_restart(running: Any, launcher: FakeLauncher) -> None:
    async with jarvis(running) as chooser:
        assert chooser.client.model_set("anthropic", "claude-x", "high")
        await wait_for(lambda: bool(chooser.of(ModelsEvent)))
    launcher.current.exit(1)  # Pi crashes between turns
    await wait_for(lambda: running.bridge._process is None)  # pyright: ignore[reportPrivateUsage]


def test_a_restarted_pi_runs_the_model_and_thinking_level_last_chosen(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        async with bridge_at(launcher, tmp_path / "token") as running:
            await _choose_claude_then_restart(running, launcher)
            async with raw(running) as socket:
                await socket.send(json.dumps({"type": "send", "text": "hola"}))
                await wait_for(lambda: len(launcher.launches) == 2)
                models = await reply_to(socket, {"type": "models_list"}, "models")
                assert models["current"]["id"] == "claude-x" and models["current"]["thinking"] == "high"
                pi = launcher.current
                assert (pi.model["id"], pi.thinking) == ("claude-x", "high")
                accounts = await reply_to(socket, {"type": "auth_list"}, "auth_providers")
                assert [p["id"] for p in accounts["providers"]] == ["anthropic"]
            async with jarvis(running) as late:
                (ready,) = late.of(Ready)
                assert ready.model == "anthropic/claude-x"

    run(scenario())


@pytest.mark.parametrize("refusal", ["set_model", "set_thinking_level"])
def test_a_restarted_pi_that_refuses_the_choice_falls_back_to_the_configured_model(
    tmp_path: Path, refusal: str
) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ok"))
        async with bridge_at(launcher, tmp_path / "token") as running:
            await _choose_claude_then_restart(running, launcher)

            async def refusing(pi: FakePi) -> None:
                pi.refuse.add(refusal)

            launcher.on_launch = refusing
            async with jarvis(running) as watcher:
                assert watcher.client.send("hola")
                await wait_for(lambda: bool(watcher.of(ModelsEvent)))
                (models,) = watcher.of(ModelsEvent)
                assert models.current == ModelRef("openai", "test-model", "Test Model", thinking="low")
                pi = launcher.current
                assert (pi.model["id"], pi.thinking) == ("test-model", "low")
                assert running.bridge.model == "openai/test-model"
            async with raw(running) as socket:
                accounts = await reply_to(socket, {"type": "auth_list"}, "auth_providers")
                assert [p["id"] for p in accounts["providers"]] == ["openai"]

    run(scenario())


# --- 4. A model_set that fails half way ---------------------------------------------------


@pytest.mark.parametrize("case", ["unlisted_level", "refused_level"])
def test_a_model_set_that_fails_half_way_leaves_pi_on_the_previous_model(tmp_path: Path, case: str) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as asker, jarvis(running) as other:
                await wait_for(lambda: running.hub.size == 2)
                if case == "refused_level":
                    await running.bridge.models()  # starts Pi
                    launcher.current.refused_levels.add("medium")
                # claude-x reasons but Pi does not list "max" for it; "medium" Pi refuses.
                level = "max" if case == "unlisted_level" else "medium"
                assert asker.client.model_set("anthropic", "claude-x", level)
                await wait_for(lambda: bool(asker.of(ErrorEvent)))
                assert [e.code for e in asker.of(ErrorEvent)] == ["thinking_unavailable"]
                await asyncio.sleep(0.1)
                assert not other.of(ErrorEvent) and not other.of(ModelsEvent)
                pi = launcher.current
                assert (pi.model["id"], pi.thinking) == ("test-model", "low")
                assert running.bridge.model == "openai/test-model"
            async with jarvis(running) as late:
                (ready,) = late.of(Ready)
                assert ready.model == "openai/test-model"
                assert late.client.models_list()
                await wait_for(lambda: bool(late.of(ModelsEvent)))
                assert late.of(ModelsEvent)[0].current == ModelRef("openai", "test-model", "Test Model",
                                                                   thinking="low")

    run(scenario())


# --- 5. A token file that is not UTF-8 ----------------------------------------------------

SECRET_BYTES = b"\xffs3cr3t\xfe\xfa"


def test_a_token_file_that_is_not_utf8_is_a_token_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "token"
    path.write_bytes(SECRET_BYTES)
    path.chmod(0o600)
    with pytest.raises(TokenError, match="UTF-8"):
        load_or_create_token(path)
    code = bridge_main.main(["--token-file", str(path), "--port", "0", "--pi", "/nonexistent/pi"])
    err = capsys.readouterr().err
    assert code == 2
    assert err.count("\n") == 1 and err.startswith("pibridge: ") and "UTF-8" in err
    assert "Traceback" not in err and "s3cr3t" not in err and "0xff" not in err and "\\xff" not in err


def test_a_valid_token_file_is_still_read(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("tók-en\n", encoding="utf-8")
    path.chmod(0o600)
    assert load_or_create_token(path) == "tók-en"


# --- 6. A client whose connection fails ---------------------------------------------------


class BrokenConnection:
    def __init__(self) -> None:
        self.attempts = 0
        self.closed: tuple[int, str] | None = None

    async def send(self, message: str) -> None:
        self.attempts += 1
        raise RuntimeError("transport broke")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


class GoodConnection:
    def __init__(self) -> None:
        self.sent: Frames = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


def test_a_client_whose_send_fails_is_logged_once_and_dropped(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        hub = Hub()
        broken, good = BrokenConnection(), GoodConnection()
        await hub.admit(broken, {"type": "ready", "secret": "frame-content"})  # type: ignore[arg-type]
        await hub.admit(good, {"type": "ready"})  # type: ignore[arg-type]
        await wait_for(lambda: hub.size == 1)
        for n in range(3):
            await hub.broadcast({"type": "message_delta", "delta": f"d{n}"})
        await wait_for(lambda: len(good.sent) == 4)
        assert [f["seq"] for f in good.sent] == [1, 2, 3, 4]
        assert broken.attempts == 1
        await wait_for(lambda: broken.closed is not None)

    with caplog.at_level(logging.WARNING, logger="jarvis.pibridge.hub"):
        run(scenario())
    warnings = [r for r in caplog.records if r.name == "jarvis.pibridge.hub"]
    assert len(warnings) == 1 and "RuntimeError" in warnings[0].getMessage()
    assert "frame-content" not in caplog.text and "transport broke" not in caplog.text


# --- 7. abort while a request waits, and shutdown --------------------------------------


def test_abort_is_not_held_up_by_the_same_clients_models_list(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(hang("..."))
        async with bridge_at(launcher, tmp_path / "token") as running, raw(running) as socket:
            await socket.send(json.dumps({"type": "send", "text": "larga"}))
            await wait_for(lambda: bool(launcher.launches) and launcher.current.running)
            launcher.current.mute.add("get_available_models")  # Pi stops answering commands
            await socket.send(json.dumps({"type": "models_list"}))
            await asyncio.sleep(0.05)
            await socket.send(json.dumps({"type": "abort"}))
            seen: Frames = []
            loop = asyncio.get_running_loop()
            start = loop.time()
            while {"type": "agent_state", "state": "idle"} not in [
                    {k: v for k, v in f.items() if k != "seq"} for f in seen]:
                seen.append(json.loads(await asyncio.wait_for(socket.recv(), 2)))
            assert loop.time() - start < 1
            assert [f.get("stopped") for f in seen if f["type"] == "message_done"] == [True]

    run(scenario())


def test_closing_the_bridge_cancels_pending_commands() -> None:
    async def scenario() -> None:
        _frames, emit = recorder()
        launcher = FakeLauncher()
        bridge = Bridge(launcher, emit, model="openai/m")
        await bridge.start()
        launcher.current.mute.add("get_available_models")
        pending = asyncio.ensure_future(bridge.models())
        await asyncio.sleep(0.05)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await bridge.close()
        with pytest.raises(ModelError):
            await asyncio.wait_for(pending, 1)
        assert loop.time() - start < COMMAND_TIMEOUT_S / 10

    run(scenario())


def test_shutdown_with_a_models_list_waiting_is_quick(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        loop = asyncio.get_running_loop()
        async with bridge_at(launcher, tmp_path / "token") as running:
            await running.bridge.start()
            launcher.current.mute.add("get_available_models")
            async with raw(running) as socket:
                await socket.send(json.dumps({"type": "models_list"}))
                await asyncio.sleep(0.05)
                start = loop.time()
        assert loop.time() - start < COMMAND_TIMEOUT_S / 10

    run(scenario())


def test_models_list_still_answers_while_the_reader_runs_free(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("hola"))
        async with bridge_at(launcher, tmp_path / "token") as running, raw(running) as socket:
            models = await reply_to(socket, {"type": "models_list"}, "models")
            assert models["current"]["id"] == "test-model"
            first = await reply_to(socket, {"type": "send", "text": "hola"}, "message_done")
            assert first["text"] == "hola"

    run(scenario())
