"""The shared model: turns, stages, lanes and targets, without any Textual import."""

from __future__ import annotations

import pytest

from jarvis.backend import (
    ActionEvent,
    Device,
    DeviceEvent,
    SnapshotEvent,
    TurnDone,
    TurnError,
    TurnStage,
    TurnStarted,
)
from jarvis.client import (
    Activity,
    ActivityEvent,
    AgentStateEvent,
    Conversation,
    ConversationsEvent,
    ConnectionStatus,
    MessageDeltaEvent,
    MessageDoneEvent,
    QueueEvent,
    Ready,
    UserMessageEvent,
)
from jarvis.model import LANE_TARGETS, STAGES, HostModel, StageState


def _done_turn(
    model: HostModel,
    turn_id: str,
    lane: str,
    total_ms: float,
    stages: dict[str, float],
    *,
    heard: str = "hola",
    device: str = "esp32-s3",
) -> None:
    model.apply_backend(TurnStarted(turn_id, device, 100.0))
    for stage, ms in stages.items():
        text = heard if stage == "stt" else None
        model.apply_backend(TurnStage(turn_id, stage, ms, text, 100.5))
    model.apply_backend(TurnDone(turn_id, lane, total_ms, f"reply {turn_id}", 101.0))


def test_module_does_not_import_textual() -> None:
    import jarvis.model

    source = open(jarvis.model.__file__, encoding="utf-8").read()
    assert "import textual" not in source and "from textual" not in source


def test_lane_targets_are_host_constants() -> None:
    assert STAGES == ("stt", "route", "agent", "tts")
    assert LANE_TARGETS == {"fast": 1.5, "chat": 4.0, "task": None}


def test_stage_progression_of_a_live_turn() -> None:
    model = HostModel()
    model.apply_backend(TurnStarted("t1", "esp32-s3", 100.0))
    turn = model.turns[-1]
    assert turn.turn_id == "t1" and turn.device_id == "esp32-s3"
    assert turn.started_at == 100.0
    assert turn.is_live
    assert [turn.stage(s).state for s in STAGES] == [
        StageState.LIVE,
        StageState.WAITING,
        StageState.WAITING,
        StageState.WAITING,
    ]

    model.apply_backend(TurnStage("t1", "stt", 310.0, "pausá la música"))
    assert turn.heard == "pausá la música"
    assert turn.stage("stt").state is StageState.DONE and turn.stage("stt").ms == 310.0
    assert turn.stage("route").state is StageState.LIVE

    model.apply_backend(TurnStage("t1", "route", 4.0))
    assert turn.stage("route").state is StageState.DONE
    assert turn.stage("agent").state is StageState.LIVE
    # The lane is only known at turn_done.
    assert turn.lane is None

    model.apply_backend(TurnDone("t1", "fast", 600.0, "Listo."))
    assert not turn.is_live and turn.is_done
    assert turn.lane == "fast" and turn.total_ms == 600.0 and turn.reply == "Listo."
    # A stage the lane skipped is not left live.
    assert turn.stage("agent").state is StageState.WAITING
    assert turn.stage("tts").state is StageState.WAITING


def test_a_later_stage_settles_a_skipped_one() -> None:
    model = HostModel()
    model.apply_backend(TurnStarted("t1", "esp32-s3", 100.0))
    model.apply_backend(TurnStage("t1", "stt", 400.0, "ordená"))
    model.apply_backend(TurnStage("t1", "route", 8.0))
    model.apply_backend(TurnStage("t1", "tts", 470.0))
    turn = model.turns[-1]
    assert turn.stage("agent").state is StageState.WAITING
    assert turn.stage("tts").state is StageState.DONE


def test_failed_turn_keeps_stage_and_reason() -> None:
    model = HostModel()
    model.apply_backend(TurnStarted("t9", "esp32-s3", 100.0))
    model.apply_backend(TurnError("t9", "stt", "no se entendió el audio"))
    turn = model.turns[-1]
    assert turn.is_failed and not turn.is_live and not turn.is_done
    assert turn.error_stage == "stt"
    assert turn.error_message == "no se entendió el audio"
    assert turn.stage("stt").state is StageState.FAILED
    assert turn.lane is None
    counts = model.counts()
    assert (counts.finished, counts.over_target, counts.failed) == (1, 0, 1)


def test_stage_events_for_an_unseen_turn_open_it() -> None:
    model = HostModel()
    model.apply_backend(TurnStage("late", "stt", 300.0, "hola"))
    assert model.turns[-1].turn_id == "late"
    assert model.turns[-1].heard == "hola"


def test_stage_medians_over_finished_turns() -> None:
    model = HostModel()
    assert model.stage_medians() == {s: None for s in STAGES}
    _done_turn(model, "a", "fast", 700, {"stt": 290, "route": 5})
    _done_turn(model, "b", "fast", 1100, {"stt": 330, "route": 6})
    _done_turn(model, "c", "chat", 4600, {"stt": 450, "route": 7, "agent": 3600, "tts": 550})
    _done_turn(model, "d", "chat", 2800, {"stt": 420, "route": 6, "agent": 1900, "tts": 480})
    # A live and a failed turn do not count.
    model.apply_backend(TurnStarted("live", "esp32-s3", 200.0))
    model.apply_backend(TurnStage("live", "stt", 9999.0))
    model.apply_backend(TurnStarted("bad", "esp32-s3", 201.0))
    model.apply_backend(TurnStage("bad", "stt", 8888.0))
    model.apply_backend(TurnError("bad", "route", "boom"))

    medians = model.stage_medians()
    assert medians == {"stt": 375.0, "route": 6.0, "agent": 2750.0, "tts": 515.0}


def test_over_target_boundary_is_exclusive() -> None:
    model = HostModel()
    _done_turn(model, "exact", "fast", 1500, {"stt": 300})
    _done_turn(model, "over", "fast", 1501, {"stt": 300})
    _done_turn(model, "chat-exact", "chat", 4000, {"stt": 300})
    exact, over, chat_exact = model.turns
    assert exact.target_s == 1.5 and not exact.over_target and exact.over_by_s is None
    assert over.over_target and over.over_by_s == pytest.approx(0.001)
    assert not chat_exact.over_target
    assert model.counts().over_target == 1


def test_task_lane_has_no_target() -> None:
    model = HostModel()
    _done_turn(model, "t", "task", 90_000, {"stt": 300})
    turn = model.turns[-1]
    assert turn.target_s is None and not turn.over_target and turn.over_by_s is None


def test_lane_stats_and_verdicts() -> None:
    model = HostModel()
    _done_turn(model, "f1", "fast", 700, {})
    _done_turn(model, "f2", "fast", 1100, {})
    _done_turn(model, "f3", "fast", 600, {})
    _done_turn(model, "c1", "chat", 4600, {})
    _done_turn(model, "c2", "chat", 2800, {})
    _done_turn(model, "k1", "task", 900, {})
    stats = {lane.lane: lane for lane in model.lane_stats()}
    assert list(stats) == ["fast", "chat", "task"]

    fast = stats["fast"]
    assert (fast.n, fast.median_s, fast.worst_s, fast.target_s) == (3, 0.7, 1.1, 1.5)
    assert fast.n_over == 0 and fast.verdict == "within"

    chat = stats["chat"]
    assert (chat.n, chat.median_s, chat.worst_s) == (2, 3.7, 4.6)
    assert chat.n_over == 1 and chat.verdict == "over"
    assert chat.over_by_s == pytest.approx(0.6)

    task = stats["task"]
    assert (task.n, task.target_s, task.verdict) == (1, None, "no_target")


def test_lane_without_turns_is_empty() -> None:
    stats = {lane.lane: lane for lane in HostModel().lane_stats()}
    assert stats["fast"].n == 0
    assert stats["fast"].median_s is None and stats["fast"].worst_s is None
    assert stats["fast"].verdict == "empty"


def test_counts() -> None:
    model = HostModel()
    _done_turn(model, "a", "fast", 700, {})
    _done_turn(model, "b", "chat", 4100, {})
    model.apply_backend(TurnStarted("c", "esp32-s3", 1.0))
    model.apply_backend(TurnError("c", "agent", "x"))
    model.apply_backend(TurnStarted("d", "esp32-s3", 2.0))
    counts = model.counts()
    assert (counts.finished, counts.over_target, counts.failed) == (3, 1, 1)


def test_action_attaches_to_the_open_turn_of_the_same_device() -> None:
    model = HostModel()
    model.apply_backend(TurnStarted("other", "esp32-b", 100.0))
    model.apply_backend(TurnStarted("mine", "esp32-a", 101.0))
    model.apply_backend(ActionEvent("esp32-a", "media_key", "play_pause", 101.5))
    mine = model.turn("mine")
    other = model.turn("other")
    assert mine is not None and other is not None
    assert [a.action_type for a in mine.actions] == ["media_key"]
    assert other.actions == []
    assert model.loose_actions == []


def test_action_without_an_open_turn_attaches_to_none() -> None:
    model = HostModel()
    _done_turn(model, "closed", "fast", 500, {}, device="esp32-a")
    model.apply_backend(ActionEvent("esp32-a", "volume", "+10"))
    model.apply_backend(TurnStarted("b", "esp32-b", 1.0))
    model.apply_backend(ActionEvent("esp32-a", "volume", "+20"))
    assert all(turn.actions == [] for turn in model.turns)
    assert [a.value for a in model.loose_actions] == ["+10", "+20"]


def test_devices_and_links() -> None:
    model = HostModel()
    device = Device("esp32-s3", "idle", -52, "192.168.1.40", True)
    model.apply_backend(SnapshotEvent((device,)))
    assert model.devices == {"esp32-s3": device}
    speaking = Device("esp32-s3", "speaking", -50, "192.168.1.40", True)
    model.apply_backend(DeviceEvent(speaking))
    assert model.devices["esp32-s3"].state == "speaking"

    assert model.daemon_status is ConnectionStatus.DISCONNECTED
    assert model.backend_status is ConnectionStatus.DISCONNECTED
    model.set_backend_status(ConnectionStatus.CONNECTED)
    model.set_daemon_status(ConnectionStatus.CONNECTING)
    assert model.backend_status is ConnectionStatus.CONNECTED
    assert model.daemon_status is ConnectionStatus.CONNECTING


def test_agent_conversation_and_pc_activity() -> None:
    model = HostModel()
    assert model.agent_state == "starting"
    model.apply_daemon(Ready(agent_state="idle", model="claude-sonnet-5-5"))
    model.apply_daemon(AgentStateEvent("working"))
    model.apply_daemon(
        ConversationsEvent((Conversation("c1", "Informe de redes", "2026"),), active_id="c1")
    )
    model.apply_daemon(QueueEvent(steering=("a",), follow_up=("b",)))
    assert (model.agent_state, model.agent_model) == ("working", "claude-sonnet-5-5")
    assert model.chat_title == "Informe de redes" and model.queue_total == 2

    model.apply_daemon(UserMessageEvent("u1", "Abrí el informe"))
    model.apply_daemon(MessageDeltaEvent("m1", "El informe "))
    model.apply_daemon(MessageDeltaEvent("m1", "tiene 14"))
    assert [(m.role, m.text, m.streaming) for m in model.messages] == [
        ("user", "Abrí el informe", False),
        ("assistant", "El informe tiene 14", True),
    ]
    model.apply_daemon(MessageDoneEvent("m1", "El informe tiene 14 páginas."))
    assert model.messages[-1].text == "El informe tiene 14 páginas."
    assert not model.messages[-1].streaming

    model.apply_daemon(ActivityEvent("m1", Activity("a1", "type", "Escribir", "running")))
    model.apply_daemon(ActivityEvent("m1", Activity("a1", "type", "Escribir", "done")))
    model.apply_daemon(ActivityEvent("m1", Activity("a2", "open_app", "Abrir Okular", "running")))
    assert [(a.id, a.status) for a in model.activities] == [("a2", "running"), ("a1", "done")]


def test_messages_keep_arrival_time_error_and_their_activities() -> None:
    from jarvis.client import HistoryEvent, HistoryMessage

    now = [500.0]
    model = HostModel(clock=lambda: now[0])
    model.apply_daemon(
        HistoryEvent((HistoryMessage("h1", "user", "antes", (Activity("a0", "type", "Viejo", "done"),)),))
    )
    assert model.messages[0].at is None  # history carries no time
    assert [a.id for a in model.activities_of("h1")] == ["a0"]

    model.apply_daemon(UserMessageEvent("u1", "Abrí el informe"))
    now[0] = 503.0
    model.apply_daemon(MessageDeltaEvent("m1", "Abriendo"))
    model.apply_daemon(ActivityEvent("m1", Activity("a1", "open_app", "Abrir Okular", "running")))
    model.apply_daemon(ActivityEvent("m1", Activity("a1", "open_app", "Abrir Okular", "done")))
    model.apply_daemon(ActivityEvent("m1", Activity("a2", "screenshot", "Captura", "running")))
    now[0] = 510.0
    model.apply_daemon(MessageDoneEvent("m1", "", error="tool crashed"))
    user, answer = model.messages[1:]
    assert (user.at, answer.at) == (500.0, 503.0)  # first seen, not last updated
    assert answer.error == "tool crashed" and answer.answer == "Abriendo"
    assert answer.text == "Abriendo\n[error: tool crashed]"  # the developer view's form
    assert [(a.id, a.status) for a in model.activities_of("m1")] == [("a1", "done"), ("a2", "running")]
    assert model.activities_of("nope") == []
