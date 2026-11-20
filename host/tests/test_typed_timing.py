"""Typed-prompt timing in the shared model: send, echo, first text, done."""

from __future__ import annotations

from jarvis.client import (
    MessageDeltaEvent,
    MessageDoneEvent,
    ModelRef,
    ModelsEvent,
    Ready,
    UserMessageEvent,
)
from jarvis.model import HostModel, PromptTiming


class Tick:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_timing_of_one_typed_prompt() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    assert model.last_timing is None
    model.note_sent()
    tick.now = 100.2
    model.apply_daemon(UserMessageEvent("u1", "hola"))
    tick.now = 101.0
    model.apply_daemon(MessageDeltaEvent("a1", ""))  # an empty delta is not text yet
    tick.now = 101.5
    model.apply_daemon(MessageDeltaEvent("a1", "o"))
    tick.now = 101.9
    model.apply_daemon(MessageDeltaEvent("a1", "k"))
    assert model.last_timing is None  # still running
    tick.now = 103.0
    model.apply_daemon(MessageDoneEvent("a1", "ok"))
    timing = model.last_timing
    assert isinstance(timing, PromptTiming)
    assert round(timing.echo_s or 0, 3) == 0.2
    assert round(timing.first_text_s or 0, 3) == 1.5
    assert round(timing.total_s or 0, 3) == 3.0
    assert timing.summary() == "first text 1.5s · total 3.0s"


def test_a_reply_without_text_has_no_first_text_figure() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u1", "x"))
    tick.now = 102.0
    model.apply_daemon(MessageDoneEvent("a1", "", error="boom"))
    timing = model.last_timing
    assert timing is not None and timing.first_text_s is None and timing.error == "boom"
    assert timing.summary() == "first text — · total 2.0s"


def test_messages_not_sent_from_here_are_not_timed() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    model.apply_daemon(UserMessageEvent("u1", "from the desktop app"))
    model.apply_daemon(MessageDeltaEvent("a1", "hi"))
    model.apply_daemon(MessageDoneEvent("a1", "hi"))
    assert model.last_timing is None


def test_queued_sends_are_matched_to_echoes_in_order() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    model.note_sent()
    tick.now = 101.0
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u1", "first"))
    model.apply_daemon(MessageDoneEvent("a1", "one"))
    assert round(model.last_timing.total_s or 0, 3) == 1.0  # type: ignore[union-attr]
    tick.now = 104.0
    model.apply_daemon(UserMessageEvent("u2", "second"))
    tick.now = 105.0
    model.apply_daemon(MessageDoneEvent("a2", "two"))
    assert round(model.last_timing.total_s or 0, 3) == 4.0  # type: ignore[union-attr]


def test_reconnect_drops_unechoed_sends() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    model.note_sent()
    model.apply_daemon(Ready(agent_state="idle"))
    model.apply_daemon(UserMessageEvent("u1", "elsewhere"))
    model.apply_daemon(MessageDoneEvent("a1", "x"))
    assert model.last_timing is None


def test_models_reply_sets_the_current_model_and_thinking() -> None:
    model = HostModel()
    model.apply_daemon(Ready(agent_state="idle", model="GPT-5.4"))
    model.apply_daemon(ModelsEvent(current=ModelRef("openai", "gpt-5.4", "GPT-5.4", "low")))
    assert model.agent_model == "GPT-5.4"
    assert model.agent_thinking == "low"
    assert model.agent_model_ref == ModelRef("openai", "gpt-5.4", "GPT-5.4", "low")
