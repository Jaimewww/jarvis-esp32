"""Typed-prompt timing in the shared model: send, echo, first text, done."""

from __future__ import annotations

from jarvis.client import (
    AgentStateEvent,
    ConnectionStatus,
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


def _tool_turn(model: HostModel, tick: Tick, *, idle: bool = True) -> None:
    """A tool-using reply: an empty message (the tool call), then a second one with the text."""

    model.note_sent()
    model.apply_daemon(AgentStateEvent("working"))
    tick.now = 100.1
    model.apply_daemon(UserMessageEvent("u1", "abre spotify"))
    model.apply_daemon(MessageDeltaEvent("t1", ""))
    tick.now = 101.0
    model.apply_daemon(MessageDoneEvent("t1", ""))  # the tool call: no text
    tick.now = 102.5
    model.apply_daemon(MessageDeltaEvent("a1", "No pude"))
    tick.now = 103.5
    model.apply_daemon(MessageDoneEvent("a1", "No pude abrir Spotify."))
    if idle:
        tick.now = 103.6
        model.apply_daemon(AgentStateEvent("idle"))


def test_a_tool_using_reply_is_timed_until_the_agent_is_idle() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    _tool_turn(model, tick)
    timing = model.last_timing
    assert timing is not None and timing.settled
    assert round(timing.first_text_s or 0, 3) == 2.5  # the second message's text
    assert round(timing.total_s or 0, 3) == 3.6  # until idle, past the second message
    assert timing.summary() == "first text 2.5s · total 3.6s"
    # The time belongs to the reply with the text, not to the empty tool message.
    assert model.reply_timing("a1") is timing
    assert model.reply_timing("t1") is None and model.reply_timing("u1") is None


def test_the_figure_is_provisional_until_idle() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u1", "x"))
    tick.now = 101.0
    model.apply_daemon(MessageDoneEvent("t1", ""))
    timing = model.last_timing
    assert timing is not None and not timing.settled and timing.first_text_s is None
    tick.now = 102.0
    model.apply_daemon(MessageDoneEvent("a1", "hola"))
    assert model.last_timing is timing and round(timing.first_text_s or 0, 3) == 2.0
    tick.now = 102.4
    model.apply_daemon(AgentStateEvent("idle"))
    assert timing.settled and round(timing.total_s or 0, 3) == 2.4


def test_without_idle_the_last_message_done_ends_the_figure() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    _tool_turn(model, tick, idle=False)
    timing = model.last_timing
    assert timing is not None and round(timing.total_s or 0, 3) == 3.5
    assert round(timing.first_text_s or 0, 3) == 2.5
    # The next prompt (or a reconnect, or a dropped link) settles it where it was.
    tick.now = 110.0
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u2", "otra"))
    assert timing.settled and round(timing.total_s or 0, 3) == 3.5
    assert model.reply_timing("a1") is timing


def test_a_dropped_link_or_a_stop_settles_the_figure() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    _tool_turn(model, tick, idle=False)
    timing = model.last_timing
    tick.now = 120.0
    model.set_daemon_status(ConnectionStatus.DISCONNECTED, "gone")
    assert timing is not None and timing.settled and round(timing.total_s or 0, 3) == 3.5
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u2", "y"))
    tick.now = 121.0
    model.apply_daemon(MessageDoneEvent("a2", "", stopped=True))
    stopped = model.last_timing
    assert stopped is not None and stopped.settled and stopped.stopped
    assert round(stopped.total_s or 0, 3) == 1.0
    tick.now = 125.0
    model.apply_daemon(AgentStateEvent("idle"))  # already settled: unchanged
    assert round(stopped.total_s or 0, 3) == 1.0


def test_each_typed_exchange_keeps_its_own_time() -> None:
    tick = Tick()
    model = HostModel(monotonic=tick)
    model.apply_daemon(UserMessageEvent("u0", "from elsewhere"))
    model.apply_daemon(MessageDoneEvent("a0", "untimed"))
    model.apply_daemon(AgentStateEvent("idle"))
    for n, seconds in ((1, 2.9), (2, 1.2)):
        model.note_sent()
        start = tick.now
        model.apply_daemon(UserMessageEvent(f"u{n}", "hola"))
        tick.now = start + seconds
        model.apply_daemon(MessageDoneEvent(f"a{n}", "ok"))
        model.apply_daemon(AgentStateEvent("idle"))
    assert model.reply_timing("a0") is None
    assert round(model.reply_timing("a1").total_s or 0, 3) == 2.9  # type: ignore[union-attr]
    assert round(model.reply_timing("a2").total_s or 0, 3) == 1.2  # type: ignore[union-attr]
