"""S7: an extension's question to the user becomes an ``ask``; the answer goes back to Pi."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fake_pi import FakeLauncher, asks
from pibridge_harness import bridge_at, jarvis, raw, run, wait_for

from jarvis.client import AgentStateEvent, Ask, AskEvent, AskResolvedEvent, MessageDoneEvent
from jarvis.pibridge.turns import Bridge


def test_a_confirm_is_an_ask_and_its_answer_reaches_pi_and_resolves_every_client(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(asks("confirm", final="Enviado.", title="Send this message?",
                                     message="pc_key ctrl+enter in Slack", timeout=5000))
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as first, jarvis(running) as second:
                await wait_for(lambda: running.hub.size == 2)
                assert first.client.send("envíalo")
                await wait_for(lambda: bool(first.of(AskEvent)) and bool(second.of(AskEvent)))
                for probe in (first, second):
                    (event,) = probe.of(AskEvent)
                    assert event.ask == Ask("ui-1", "confirm", "Send this message?",
                                            "pc_key ctrl+enter in Slack", timeout_ms=5000)
                assert first.client.ui_response("ui-1", confirmed=True)
                for probe in (first, second):
                    await wait_for(lambda p=probe: bool(p.of(AskResolvedEvent)))  # type: ignore[misc]
                    assert [e.request_id for e in probe.of(AskResolvedEvent)] == ["ui-1"]
                    await wait_for(lambda p=probe: any(  # type: ignore[misc]
                        isinstance(e, AgentStateEvent) and e.state == "idle" for e in p.events))
                    assert [e.text for e in probe.of(MessageDoneEvent)] == ["Enviado."]
                pi = launcher.current
                assert pi.last_answer == {"type": "extension_ui_response", "id": "ui-1", "confirmed": True}
                # A late second answer to the same question changes nothing.
                assert second.client.ui_response("ui-1", confirmed=False)
                await asyncio.sleep(0.1)
                answers = [r for r in pi.written if r["type"] == "extension_ui_response"]
                assert answers == [pi.last_answer]
                assert len(first.of(AskResolvedEvent)) == 1

    run(scenario())


def _answer_case(tmp_path: Path, method: str, fields: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any] | None:
    async def scenario() -> dict[str, Any] | None:
        launcher = FakeLauncher(asks(method, title="Pick", **fields))
        async with bridge_at(launcher, tmp_path / "token") as running, raw(running) as socket:
            await socket.send(json.dumps({"type": "send", "text": "go"}))
            while True:
                frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
                if frame["type"] == "ask":
                    assert frame["ask"]["method"] == method and frame["ask"]["requestId"] == "ui-1"
                    assert frame["ask"]["title"] == "Pick"
                    await socket.send(json.dumps({"type": "ui_response", "requestId": "ui-1", **answer}))
                if frame["type"] == "ask_resolved":
                    assert frame["requestId"] == "ui-1"
                if frame == {"type": "agent_state", "state": "idle", "seq": frame["seq"]}:
                    return launcher.current.last_answer

    return run(scenario())


def test_select_and_input_answers_carry_the_value_and_a_refusal_cancels(tmp_path: Path) -> None:
    select = {"options": ["Allow", "Block"]}
    assert _answer_case(tmp_path, "select", select, {"value": "Allow"}) == {
        "type": "extension_ui_response", "id": "ui-1", "value": "Allow"}
    assert _answer_case(tmp_path, "input", {"placeholder": "name"}, {"value": "Jarvis"}) == {
        "type": "extension_ui_response", "id": "ui-1", "value": "Jarvis"}
    # The Jarvis clients only send ``confirmed``: without a value, select and input are cancelled.
    assert _answer_case(tmp_path, "select", select, {"confirmed": True}) == {
        "type": "extension_ui_response", "id": "ui-1", "cancelled": True}
    assert _answer_case(tmp_path, "confirm", {}, {"confirmed": False}) == {
        "type": "extension_ui_response", "id": "ui-1", "confirmed": False}
    assert _answer_case(tmp_path, "confirm", {}, {"cancelled": True}) == {
        "type": "extension_ui_response", "id": "ui-1", "cancelled": True}


def test_with_no_client_connected_a_question_is_denied() -> None:
    async def scenario() -> None:
        frames: list[dict[str, Any]] = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        launcher = FakeLauncher(asks("confirm", title="Run it?"), asks("input", title="Name?"))
        bridge = Bridge(launcher, emit, model="openai/test-model")
        await bridge.send("hazlo")  # nobody is connected
        await wait_for(lambda: {"type": "agent_state", "state": "idle"} in frames)
        pi = launcher.current
        assert pi.last_answer == {"type": "extension_ui_response", "id": "ui-1", "cancelled": True}
        assert not [f for f in frames if f["type"] in ("ask", "ask_resolved")]
        # A fire-and-forget request is not a question: no ask, no answer.
        pi.emit({"type": "extension_ui_request", "id": "n1", "method": "notify", "message": "hi"})
        # A question pending when the last client leaves is denied too.
        await bridge.clients_changed(1)
        frames.clear()
        await bridge.send("otra")
        await wait_for(lambda: any(f["type"] == "ask" for f in frames))
        assert pi.last_answer is not None and pi.last_answer["id"] == "ui-1"
        await bridge.clients_changed(0)
        await wait_for(lambda: {"type": "agent_state", "state": "idle"} in frames)
        assert pi.last_answer == {"type": "extension_ui_response", "id": "ui-2", "cancelled": True}
        assert {"type": "ask_resolved", "requestId": "ui-2"} in frames
        answers = [r for r in pi.written if r["type"] == "extension_ui_response"]
        assert [a["id"] for a in answers] == ["ui-1", "ui-2"]
        await bridge.close()

    run(scenario())
