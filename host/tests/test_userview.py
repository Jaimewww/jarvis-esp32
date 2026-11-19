"""The user view, without Textual: exchanges from the shared model, and their rows."""

from __future__ import annotations

import re

import pytest

from jarvis.backend import (
    ActionEvent,
    Device,
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
    ConnectionStatus,
    MessageDeltaEvent,
    MessageDoneEvent,
    QueueEvent,
    Ready,
    UserMessageEvent,
)
from jarvis.model import HostModel
from jarvis.userview import Exchange, exchanges, render_rows

DEVICE = "esp32-s3"
#: Developer detail the user view must never show.
FORBIDDEN = (
    r"\bms\b", r"\bstt\b", r"\btts\b", r"\broute\b", r"\blane\b", r"\bfast\b", r"\bchat\b",
    r"dBm", r"\d+\.\d+\.\d+\.\d+", r"media_key", r"open_app", r"daemon", r"backend",
    r"abc12345", r"t-voice", r"claude",
)


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _voice(
    model: HostModel, turn_id: str, ts: float, heard: str | None, reply: str, actions: tuple[tuple[str, str], ...] = ()
) -> None:
    model.apply_backend(TurnStarted(turn_id, DEVICE, ts))
    if heard is not None:
        model.apply_backend(TurnStage(turn_id, "stt", 300, heard, ts + 0.3))
    for kind, value in actions:
        model.apply_backend(ActionEvent(DEVICE, kind, value, ts + 0.4))
    model.apply_backend(TurnDone(turn_id, "fast", 600, reply, ts + 0.6))


def _model() -> tuple[HostModel, Clock]:
    clock = Clock()
    model = HostModel(clock=clock)
    model.set_backend_status(ConnectionStatus.CONNECTED)
    model.set_daemon_status(ConnectionStatus.CONNECTED)
    model.apply_backend(SnapshotEvent((Device(DEVICE, "idle", -52, "192.168.1.40", True),)))
    model.apply_daemon(Ready(agent_state="idle", model="claude-sonnet-5-5"))
    return model, clock


def _text(rows: list[list[tuple[str, str]]]) -> list[str]:
    return ["".join(t for t, _ in row) for row in rows]


def test_voice_exchange_with_its_action() -> None:
    model, _ = _model()
    _voice(model, "t-voice-1", 990.0, "pausá la música", "Listo.", (("media_key", "play_pause"),))
    [exchange] = exchanges(model)
    assert exchange == Exchange(
        source="voice", at=990.0, said="pausá la música", answer="Listo.",
        did=[("done", "played or paused the music")], live=False,
    )


def test_typed_exchange_from_daemon_messages_and_activity() -> None:
    model, clock = _model()
    model.apply_daemon(UserMessageEvent("u1", "Abrí el informe"))
    clock.now += 1
    model.apply_daemon(MessageDeltaEvent("m1", "El informe tiene "))
    model.apply_daemon(ActivityEvent("m1", Activity("a1", "open_app", "Abrir Okular", "done")))
    model.apply_daemon(MessageDeltaEvent("m1", "14 páginas."))
    model.apply_daemon(ActivityEvent("m1", Activity("a2", "type", "Escribir en el documento", "running")))
    model.apply_daemon(AgentStateEvent("working"))
    [exchange] = exchanges(model)
    assert exchange.source == "typed" and exchange.said == "Abrí el informe"
    assert exchange.answer == "El informe tiene 14 páginas."
    assert exchange.did == [("done", "Abrir Okular"), ("live", "Escribir en el documento")]
    assert exchange.live


def test_failed_exchanges_say_what_to_do_next() -> None:
    model, clock = _model()
    model.apply_backend(TurnStarted("t-fail", DEVICE, 995.0))
    model.apply_backend(TurnError("t-fail", "stt", "no se entendió el audio", 995.5))
    model.apply_daemon(UserMessageEvent("u1", "Completá el resumen"))
    model.apply_daemon(MessageDoneEvent("m1", "", error="model overloaded"))
    voice, typed = exchanges(model)
    assert voice.failure == (
        "Couldn't hear you.", "Say it again.", "the voice device is still listening, so just talk again"
    )
    assert typed.failure is not None and typed.failure[0] == "Couldn't finish the answer."
    assert "type it again" in typed.failure[2]


def test_voice_and_typed_exchanges_merge_in_time_order() -> None:
    model, clock = _model()
    _voice(model, "t-voice-1", 990.0, "subí el volumen", "Volumen al 60.", (("volume", "up"),))
    clock.now = 1_000.0
    model.apply_daemon(UserMessageEvent("u1", "Abrí el informe"))
    model.apply_daemon(MessageDoneEvent("m1", "Listo, abierto."))
    _voice(model, "t-voice-2", 1_010.0, "qué hora es", "Son las once.")
    assert [(e.source, e.said) for e in exchanges(model)] == [
        ("voice", "subí el volumen"), ("typed", "Abrí el informe"), ("voice", "qué hora es")
    ]


def test_a_voice_turn_echoed_by_the_daemon_shows_once() -> None:
    # If the agent behind a voice turn is Gentle Dot, the daemon echoes the heard text
    # as a user_message (client.send docstring). It must fold into the voice exchange.
    model, clock = _model()
    model.apply_backend(TurnStarted("t-voice-1", DEVICE, 999.0))
    model.apply_backend(TurnStage("t-voice-1", "stt", 300, "Ordená las descargas", 999.3))
    clock.now = 1_000.0
    model.apply_daemon(UserMessageEvent("u1", "ordená las descargas."))
    model.apply_daemon(MessageDeltaEvent("m1", "Lo hago en segundo plano."))
    model.apply_daemon(ActivityEvent("m1", Activity("a1", "files", "Ordenar descargas por tipo", "background")))
    [exchange] = exchanges(model)
    assert exchange.source == "voice" and exchange.said == "Ordená las descargas"
    assert exchange.answer == "Lo hago en segundo plano."
    assert exchange.did == [("background", "Ordenar descargas por tipo")]
    # The same text typed much later is a typed exchange of its own.
    clock.now = 2_000.0
    model.apply_daemon(UserMessageEvent("u2", "ordená las descargas"))
    assert [e.source for e in exchanges(model)] == ["voice", "typed"]


def test_rows_show_the_story_without_developer_detail() -> None:
    model, clock = _model()
    _voice(model, "t-voice-1", 990.0, "pausá la música", "Listo.", (("media_key", "play_pause"),))
    model.apply_backend(TurnStarted("t-voice-2", DEVICE, 995.0))
    model.apply_backend(TurnError("t-voice-2", "stt", "no se entendió el audio", 995.5))
    clock.now = 1_000.0
    model.apply_daemon(UserMessageEvent("u1", "Completá las conclusiones"))
    model.apply_daemon(MessageDeltaEvent("m1", "Estoy redactando el cierre"))
    model.apply_daemon(ActivityEvent("m1", Activity("a1", "type", "writing in the document", "running")))
    model.apply_daemon(AgentStateEvent("working"))
    model.apply_daemon(QueueEvent(follow_up=("otra cosa",)))
    rows = render_rows(model, 140, 40)
    text = _text(rows)
    screen = "\n".join(text)
    assert len(rows) == 40 and all(len(line) == 140 for line in text)
    # Header: Jarvis card (state in block letters, what and the queue), voice device card.
    assert "╭─ Jarvis" in text[0] and "╭─ Voice device  esp32-s3" in text[0]
    assert "on the message you typed" in screen and "1 more message waiting its turn" in screen
    assert "Ready" in screen and "connected to this PC" in screen and "good signal" in screen
    assert "█" in screen or "▀" in screen  # the block letters
    # Thread: rail with time and origin, bubbles on the right, Jarvis answers with ◆.
    said = next(line for line in text if "pausá la música" in line)
    assert re.match(r"\s+\d\d:\d\d\s+said\s+◎", said) and said.rstrip().endswith("pausá la música")
    assert any(line.strip() == "│ ◆ Listo." for line in text)
    assert any("on your PC  ✓ played or paused the music" in line for line in text)
    assert any("✗ Couldn't hear you. Say it again." in line for line in text)
    assert "the voice device is still listening, so just talk again" in screen
    typed = next(line for line in text if "Completá las conclusiones" in line)
    assert re.match(r"\s+\d\d:\d\d\s+typed\s+▸", typed)
    # The live exchange: accent rail, cursor, the running action with how to stop it.
    live = next(line for line in text if "Estoy redactando el cierre" in line)
    assert "┃ ◆ Estoy redactando el cierre▍" in live
    assert any("┃" in line and "◐ writing in the document" in line and "ctrl+a stops it" in line for line in text)
    assert "Today" in screen
    for pattern in FORBIDDEN:
        assert not re.search(pattern, screen), pattern


def test_live_exchange_is_emphasised_with_live_classes() -> None:
    model, _ = _model()
    model.apply_daemon(UserMessageEvent("u1", "Hola"))
    model.apply_daemon(MessageDeltaEvent("m1", "Buenas"))
    rows = render_rows(model, 140, 30)
    line = next(row for row in rows if "Buenas" in "".join(t for t, _ in row))
    classes = {cls for text, cls in line if text.strip()}
    assert any("lv" in cls.split() for cls in classes)  # the live rail and cursor
    assert any(text == "Buenas" and "b" in cls.split() for text, cls in line)  # bold answer


def test_scrolled_thread_says_how_many_are_above() -> None:
    model, _ = _model()
    for i in range(12):
        _voice(model, f"t{i}", 900.0 + i, f"pedido {i}", f"respuesta {i}")
    text = _text(render_rows(model, 140, 30))
    divider = next(line for line in text if "Today" in line)
    hidden = int(re.search(r"(\d+) earlier above", divider).group(1))  # type: ignore[union-attr]
    assert hidden > 0
    assert "pedido 11" in "\n".join(text) and "pedido 0" not in "\n".join(text)
    assert sum(1 for line in text if "pedido" in line) == 12 - hidden


def test_empty_model_shows_empty_states_not_demo_data() -> None:
    model = HostModel()
    screen = "\n".join(_text(render_rows(model, 140, 40)))
    assert "Nothing here yet" in screen
    assert "No voice device yet" in screen
    assert "WORKING" not in screen and "esp32-s3" not in screen and "Jaime" not in screen


@pytest.mark.parametrize("size", [(139, 40), (120, 30), (100, 30), (80, 24), (60, 20), (30, 10), (1, 1)])
def test_narrow_windows_degrade_without_crashing(size: tuple[int, int]) -> None:
    model, clock = _model()
    _voice(model, "t1", 990.0, "pausá la música " * 6, "Listo. " * 30, (("open_app", "firefox"),))
    model.apply_daemon(UserMessageEvent("u1", "Completá las conclusiones con un resumen " * 4))
    model.apply_daemon(MessageDeltaEvent("m1", "Estoy redactando " * 20))
    model.apply_daemon(AgentStateEvent("working"))
    w, h = size
    for frame in (0, 1, 7):
        rows = render_rows(model, w, h, frame=frame)
        assert len(rows) == h
        assert all(sum(len(t) for t, _ in row) == w for row in rows), size
    if w >= 80:
        screen = "\n".join(_text(render_rows(model, w, h)))
        assert "Voice device" in screen and "Estoy redactando" in screen
