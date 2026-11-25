"""S6: Pi's tool runs become the ``activity`` frames the clients show and count."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fake_pi import FakeLauncher, reply, tool_turn
from pibridge_harness import bridge_at, jarvis, run, wait_for

from jarvis.client import ActivityEvent, MessageDoneEvent
from jarvis.pibridge.turns import Bridge

Frames = list[dict[str, Any]]


def make(launcher: FakeLauncher) -> tuple[Bridge, Frames]:
    frames: Frames = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    return Bridge(launcher, emit, model="openai/test-model"), frames


def idle(frames: Frames) -> bool:
    return {"type": "agent_state", "state": "idle"} in frames


def test_a_tool_turn_is_an_empty_message_the_activity_running_then_done_and_the_answer() -> None:
    async def scenario() -> None:
        bridge, frames = make(FakeLauncher(tool_turn("pc_media", {"action": "volume_up"}, final="Listo.")))
        await bridge.send("sube el volumen")
        await wait_for(lambda: idle(frames))
        assert [f["type"] for f in frames] == [
            "agent_state", "user_message", "message_done", "activity", "activity",
            "message_delta", "message_done", "agent_state",
        ]
        calling, running, done, delta, answer = frames[2], frames[3], frames[4], frames[5], frames[6]
        # The message that only calls the tool still ends, empty (L5).
        assert calling["text"] == "" and not calling.get("error") and not calling.get("stopped")
        activity = {"id": "call_1", "kind": "pc_media", "title": "pc_media"}
        assert running == {"type": "activity", "messageId": calling["messageId"],
                           "activity": {**activity, "status": "running"}}
        assert done == {"type": "activity", "messageId": calling["messageId"],
                        "activity": {**activity, "status": "done"}}
        assert delta["messageId"] == answer["messageId"] != calling["messageId"]
        assert answer["text"] == "Listo."
        await bridge.close()

    run(scenario())


def test_a_tool_that_fails_is_reported_failed() -> None:
    async def scenario() -> None:
        bridge, frames = make(FakeLauncher(tool_turn("pc_run", is_error=True, final="No pude.")))
        await bridge.send("abre algo")
        await wait_for(lambda: idle(frames))
        statuses = [f["activity"]["status"] for f in frames if f["type"] == "activity"]
        assert statuses == ["running", "failed"]
        assert [f["text"] for f in frames if f["type"] == "message_done"] == ["", "No pude."]
        await bridge.close()

    run(scenario())


def test_a_tool_still_running_when_pi_exits_ends_failed() -> None:
    async def scenario() -> None:
        async def crash_in_tool(pi: Any, record: dict[str, Any]) -> None:
            pi.respond(record, "prompt", data={"disposition": "started"})
            pi.emit({"type": "agent_start"})
            pi.emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
            pi.emit({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "toolUse"}})
            pi.emit({"type": "tool_execution_start", "toolCallId": "c9", "toolName": "pc_type", "args": {}})
            pi.exit(1)

        bridge, frames = make(FakeLauncher(crash_in_tool))
        await bridge.send("escribe")
        await wait_for(lambda: idle(frames))
        activities = [f for f in frames if f["type"] == "activity"]
        assert [a["activity"]["status"] for a in activities] == ["running", "failed"]
        assert any(f["type"] == "error" and f["code"] == "agent_exited" for f in frames)
        await bridge.close()

    run(scenario())


def test_the_unchanged_client_counts_one_tool_and_two_model_calls(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(tool_turn("pc_media", final="Subí el volumen."), reply("Hola."))
        async with bridge_at(launcher, tmp_path / "token") as running, jarvis(running) as probe:
            probe.host.note_sent()
            assert probe.client.send("sube el volumen")
            await wait_for(lambda: len(probe.traces) == 1)
            trace = probe.traces[0]
            assert trace.ended_by == "idle" and trace.error is None and not trace.stopped
            assert len(trace.calls) == 2 and len(trace.tools) == 1
            (tool,) = trace.tools
            assert (tool.kind, tool.status, tool.call) == ("pc_media", "done", 0)
            assert tool.end is not None
            assert all(call.end is not None for call in trace.calls)
            record = trace.record()
            assert (record["model_calls_n"], record["tools_n"]) == (2, 1)
            done = probe.of(MessageDoneEvent)
            assert [d.text for d in done] == ["", "Subí el volumen."]
            (activity_event, _) = probe.of(ActivityEvent)
            assert [a.status for a in probe.host.activities_of(activity_event.message_id)] == ["done"]
            # A plain answer is one model call and no tool.
            probe.host.note_sent()
            assert probe.client.send("hola")
            await wait_for(lambda: len(probe.traces) == 2)
            assert (len(probe.traces[1].calls), len(probe.traces[1].tools)) == (1, 0)

    run(scenario())
