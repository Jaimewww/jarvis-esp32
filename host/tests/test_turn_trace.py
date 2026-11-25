"""Per-stage timing of a whole typed turn: model calls, tools, human waits, the log line."""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.client import (
    Activity,
    ActivityEvent,
    AgentStateEvent,
    ConnectionStatus,
    MessageDeltaEvent,
    MessageDoneEvent,
    UserMessageEvent,
)
from jarvis.model import HostModel
from jarvis.turntrace import TurnLog, TurnTrace, default_log_path


class Tick:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _tool_turn(tick: Tick, model: HostModel) -> None:
    """«abrí Spotify»: an empty tool-call message, the tool, a confirmation, then the text."""

    model.note_sent()
    tick.now = 100.1
    model.apply_daemon(UserMessageEvent("u1", "abrí Spotify"))
    tick.now = 102.0
    model.apply_daemon(MessageDoneEvent("a1", ""))  # model call 1: the tool call
    tick.now = 102.1
    model.apply_daemon(ActivityEvent("a1", Activity("t1", "open_app", "opening Spotify", "running")))
    tick.now = 102.2
    model.note_confirms({"daemon:r1"})  # the card is up: a person must answer
    tick.now = 105.2
    model.note_confirms(set())  # answered after 3 s
    tick.now = 105.6
    model.apply_daemon(ActivityEvent("a1", Activity("t1", "open_app", "opened Spotify", "done")))
    tick.now = 107.0
    model.apply_daemon(MessageDeltaEvent("a2", "Listo"))
    tick.now = 107.5
    model.apply_daemon(MessageDoneEvent("a2", "Listo."))  # model call 2: the reply
    tick.now = 107.6
    model.apply_daemon(AgentStateEvent("idle"))


def test_a_whole_tool_turn_is_timed_per_stage() -> None:
    tick = Tick()
    records: list[TurnTrace] = []
    model = HostModel(monotonic=tick, on_turn=records.append)
    _tool_turn(tick, model)
    assert len(records) == 1
    trace = records[0]
    assert trace.ended_by == "idle"
    assert round(trace.total_s or 0, 3) == 7.6
    assert round(trace.first_text_s or 0, 3) == 7.0
    calls = trace.calls
    assert [c.message_id for c in calls] == ["a1", "a2"]
    # Model call 1 starts at the echo; call 2 starts when the tool ends (the last boundary).
    assert (round(calls[0].start - 100, 3), round(calls[0].end - 100, 3)) == (0.1, 2.0)
    assert (round(calls[1].start - 100, 3), round(calls[1].end - 100, 3)) == (5.6, 7.5)
    [tool] = trace.tools
    assert (tool.kind, tool.status, round(tool.start - 100, 3), round(tool.end - 100, 3)) == (
        "open_app", "done", 2.1, 5.6
    )
    assert round(trace.human_wait_s, 3) == 3.0
    assert round(trace.model_s, 3) == round(1.9 + 1.9, 3)
    summary = trace.summary()
    assert summary == "2 calls 3.8s · 1 tool · wait 3.0s"
    assert model.last_timing is not None and model.last_timing.trace is trace


def test_the_record_is_relative_and_carries_no_prompt_text() -> None:
    tick = Tick()
    records: list[TurnTrace] = []
    model = HostModel(monotonic=tick, on_turn=records.append)
    _tool_turn(tick, model)
    record = records[0].record()
    assert record["v"] == 1 and record["ended_by"] == "idle" and record["chars"] == len("abrí Spotify")
    assert record["model_calls_n"] == 2 and record["tools_n"] == 1
    assert record["total_s"] == 7.6 and record["echo_s"] == 0.1 and record["human_wait_s"] == 3.0
    assert record["model_calls"][1] == {"start_s": 5.6, "end_s": 7.5, "first_text_s": 7.0, "error": None}
    assert record["tools"] == [{"kind": "open_app", "status": "done", "start_s": 2.1, "end_s": 5.6, "call": 0}]
    assert "Spotify" not in json.dumps(record)  # neither the prompt nor a tool title is logged


def test_a_prompt_from_another_window_is_not_recorded() -> None:
    tick = Tick()
    records: list[TurnTrace] = []
    model = HostModel(monotonic=tick, on_turn=records.append)
    model.apply_daemon(UserMessageEvent("u1", "x"))
    model.apply_daemon(MessageDoneEvent("a1", "y"))
    model.apply_daemon(AgentStateEvent("idle"))
    assert records == []


def test_a_dropped_link_ends_the_turn_where_it_was() -> None:
    tick = Tick()
    records: list[TurnTrace] = []
    model = HostModel(monotonic=tick, on_turn=records.append)
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u1", "x"))
    tick.now = 101.0
    model.apply_daemon(MessageDoneEvent("a1", ""))
    tick.now = 101.5
    model.apply_daemon(ActivityEvent("a1", Activity("t1", "key", "pressing", "running")))
    tick.now = 103.0
    model.set_daemon_status(ConnectionStatus.DISCONNECTED, "gone")
    [trace] = records
    assert trace.ended_by == "disconnect"
    assert trace.tools[0].end is None and trace.record()["tools"][0]["end_s"] is None


def test_a_spoken_prompt_records_the_time_from_the_end_of_speech() -> None:
    tick = Tick()
    records: list[TurnTrace] = []
    model = HostModel(monotonic=tick, on_turn=records.append)
    model.note_sent(spoken=True, speech_end_at=99.7)
    model.apply_daemon(UserMessageEvent("u1", "hola"))
    model.apply_daemon(MessageDoneEvent("a1", "hola"))
    model.apply_daemon(AgentStateEvent("idle"))
    record = records[0].record()
    assert record["spoken"] is True and record["stt_s"] == 0.3


def test_the_log_appends_one_json_line_per_turn(tmp_path: Path) -> None:
    tick = Tick()
    path = tmp_path / "state" / "jarvis" / "turns.jsonl"
    log = TurnLog(path)
    model = HostModel(monotonic=tick, on_turn=log.write)
    _tool_turn(tick, model)
    _tool_turn(tick, model)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["model_calls_n"] == 2
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_log_that_cannot_be_written_never_breaks_the_turn(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    log = TurnLog(blocker / "turns.jsonl")  # a file where a directory should be
    tick = Tick()
    model = HostModel(monotonic=tick, on_turn=log.write)
    _tool_turn(tick, model)
    assert model.last_timing is not None and model.last_timing.settled
    assert log.failed


def test_default_log_path_follows_xdg_state_home_and_can_be_turned_off() -> None:
    assert default_log_path({"XDG_STATE_HOME": "/s"}) == Path("/s/jarvis/turns.jsonl")
    assert default_log_path({"HOME": "/h"}) == Path("/h/.local/state/jarvis/turns.jsonl")
    assert default_log_path({"JARVIS_TURN_LOG": "/x/t.jsonl"}) == Path("/x/t.jsonl")
    assert default_log_path({"JARVIS_TURN_LOG": "off"}) is None


def test_the_developer_view_shows_the_turn_in_one_line() -> None:
    from jarvis.devview import render_rows

    tick = Tick()
    model = HostModel(monotonic=tick)
    model.set_daemon_status(ConnectionStatus.CONNECTED)
    _tool_turn(tick, model)
    text = ["".join(t for t, _ in row) for row in render_rows(model, 200, 50)]
    [turn] = [line for line in text if " turn " in line and " calls " in line]
    assert "2 calls 3.8s · 1 tool · wait 3.0s" in turn
