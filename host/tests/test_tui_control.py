"""PC control in the window (S13): ``/control`` and confirmations go through ``jarvis-input``.

The helper is ``FakeHelper`` on ``control.sock`` in a temp ``XDG_RUNTIME_DIR``
(``conftest``); the daemon is the scripted fake; the clock is a fake one shared by the
window and the helper, so the 30 minutes pass at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import fake_server, wait_for
from fake_gentle_dot import FakeDaemon
from fake_jarvis_input import FakeHelper
from test_tui_voice import FakeRecorder, FakeTranscriber

from jarvis.control import revoke
from jarvis.tui import ConfirmCard, ControlBar, JarvisApp, KeysFooter

START = datetime(2026, 11, 20, 15, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


async def _idle_backend(socket: Any) -> None:
    await socket.wait_closed()


Steps = Callable[[Any, JarvisApp, dict[str, Any]], Awaitable[None]]


def _run(tmp_path: Path, runtime: Path, steps: Steps, helper: bool = True, **kwargs: Any) -> dict[str, Any]:
    token = tmp_path / "token"
    token.write_text("t\n", encoding="utf-8")
    daemon = FakeDaemon()
    clock = Clock()
    fake = FakeHelper(runtime, clock=clock)
    out: dict[str, Any] = {"daemon": daemon, "clock": clock, "helper": fake, "recorders": []}

    def factory(path: Path) -> FakeRecorder:
        recorder = FakeRecorder(path)
        out["recorders"].append(recorder)
        return recorder

    async def scenario() -> None:
        if helper:
            await fake.start()
        try:
            async with fake_server(_idle_backend) as port, fake_server(daemon.handler) as daemon_port:
                app = JarvisApp(
                    url=f"ws://127.0.0.1:{daemon_port}/ws",
                    token_path=token,
                    backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                    backend_token="t",
                    animate=False,
                    recorder_factory=factory,
                    transcriber=FakeTranscriber(),
                    control_clock=clock,
                    **{"confirm_arm_s": 0.0, "control_backoff": (0.05, 0.1), **kwargs},
                )
                async with app.run_test(size=(120, 36)) as pilot:
                    await wait_for(lambda: app._host.agent_thinking is not None, timeout=5.0)
                    if helper:
                        await wait_for(lambda: app._control_link.connected and bool(fake.subscribers))
                    await steps(pilot, app, out)
                    out["notes"] = [n.message for n in app._notifications]
        finally:
            out["ops"] = [m["op"] for m in fake.received]
            await fake.stop()

    asyncio.run(scenario())
    return out


async def _type(pilot: Any, text: str) -> None:
    await pilot.press(*text, "enter")
    await pilot.pause()


def _sends(daemon: FakeDaemon) -> list[dict[str, Any]]:
    return [m for m in daemon.received if m["type"] == "send"]


def _responses(daemon: FakeDaemon) -> list[dict[str, Any]]:
    return [m for m in daemon.received if m["type"] == "ui_response"]


def _requests(helper: FakeHelper) -> list[str]:
    return [m["op"] for m in helper.received if m["op"] not in ("subscribe", "status", "answer")]


def _notes(app: JarvisApp) -> list[str]:
    return [n.message for n in app._notifications]


CONFIRM = {
    "type": "ask",
    "ask": {
        "requestId": "r1",
        "method": "confirm",
        "title": "Send this message?",
        "message": "pc_key ctrl+enter in Slack",
        "timeoutMs": 30000,
    },
}


# --- /control -------------------------------------------------------------------


def test_control_on_asks_the_helper_and_shows_it_in_both_views(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        bar = app.query_one(ControlBar)
        helper: FakeHelper = out["helper"]
        assert not bar.display
        assert "f5 control" in app.query_one(KeysFooter).render().plain
        await _type(pilot, "/control on")
        await wait_for(lambda: bar.display)
        out["grants"] = helper.ops("grant")
        out["user_bar"] = bar.render().plain
        await pilot.press("f2")
        await pilot.pause()
        out["dev_bar"] = bar.render().plain if bar.display else ""
        out["clock"].now = START + timedelta(seconds=48)
        app._control_tick()
        out["later"] = bar.render().plain
        out["before_exit"] = _requests(helper)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["grants"] == [{"op": "grant", "minutes": 30}]
    assert "◉ control on · 30:00 left · /control off" in out["user_bar"]
    assert "◉ control on · 30:00 left · /control off" in out["dev_bar"]
    assert "29:12 left" in out["later"]
    assert _sends(out["daemon"]) == []  # /control is never chat
    assert any("Control on for 30 min" in note for note in out["notes"])
    assert out["before_exit"] == ["grant"]
    assert out["ops"][-1] == "revoke"  # revoked when the window closes
    assert not (_private_runtime_dir / "jarvis").exists()  # no permission file anymore


def test_control_status_and_off(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        bar = app.query_one(ControlBar)
        await _type(pilot, "/control status")
        await wait_for(lambda: any(n.startswith("Control is off") for n in _notes(app)))
        await _type(pilot, "/control on")
        await wait_for(lambda: bar.display)
        out["clock"].now = START + timedelta(seconds=48)
        await _type(pilot, "/control status")
        await wait_for(lambda: any("29:12 left" in n for n in _notes(app)))
        await _type(pilot, "/control off")
        await wait_for(lambda: "Control off." in _notes(app))
        out["enabled"] = out["helper"].enabled
        out["bar"] = bar.display
        await _type(pilot, "/control maybe")
        await wait_for(lambda: any("/control on · /control off · /control status" in n for n in _notes(app)))

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["enabled"] is False and out["bar"] is False
    assert not any("revoked" in note for note in out["notes"])  # our own off is not a surprise
    assert _sends(out["daemon"]) == []


def test_countdown_end_hides_the_bar_without_revoking(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        bar = app.query_one(ControlBar)
        await _type(pilot, "/control on")
        await wait_for(lambda: bar.display)
        out["clock"].now = START + timedelta(minutes=30, seconds=1)
        app._control_tick()
        await pilot.pause()
        out["bar"] = bar.display
        await out["helper"].push_state()  # the helper's own expiry
        await asyncio.sleep(0.1)
        out["requests"] = _requests(out["helper"])

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["bar"] is False
    assert out["requests"] == ["grant"]  # the helper is the source of truth
    assert sum("control expired" in note for note in out["notes"]) == 1


def test_revoked_by_the_stop_key_hides_the_indicator(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        bar = app.query_one(ControlBar)
        await _type(pilot, "/control on")
        await wait_for(lambda: bar.display)
        await revoke()  # what jarvis-stop does
        await wait_for(lambda: not bar.display)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert any(note.startswith("Control off: jarvis-input revoked it") for note in out["notes"])


def test_f5_turns_control_on_and_off(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        helper: FakeHelper = out["helper"]
        await pilot.press("f5")
        await wait_for(lambda: helper.enabled and app.query_one(ControlBar).display)
        await pilot.press("f5")
        await wait_for(lambda: not helper.enabled and not app.query_one(ControlBar).display)
        out["requests"] = _requests(helper)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["requests"] == ["grant", "revoke"]


def test_helper_not_running_says_so_and_the_bar_shows_unavailable(
    tmp_path: Path, _private_runtime_dir: Path
) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        bar = app.query_one(ControlBar)
        out["quiet_at_start"] = not bar.display
        await _type(pilot, "/control on")
        await wait_for(lambda: bar.display)
        out["bar"] = bar.render().plain
        # The helper comes up: the link connects and the bar clears.
        await out["helper"].start()
        await wait_for(lambda: app._control_link.connected and not bar.display, timeout=5.0)

    out = _run(tmp_path, _private_runtime_dir, steps, helper=False)
    assert out["quiet_at_start"]
    assert "control unavailable" in out["bar"] and "jarvis-input is not running" in out["bar"]
    assert any(note.startswith("jarvis-input is not running: ") for note in out["notes"])
    assert _sends(out["daemon"]) == []


def test_control_refuses_without_xdg_runtime_dir(
    tmp_path: Path, _private_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR")

    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await _type(pilot, "/control on")
        await wait_for(lambda: any("XDG_RUNTIME_DIR" in n for n in _notes(app)))
        out["on"] = "control on" in app.query_one(ControlBar).render().plain

    out = _run(tmp_path, _private_runtime_dir, steps, helper=False)
    assert out["on"] is False
    assert _sends(out["daemon"]) == []


def test_helper_restart_drops_control_and_the_card(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        helper: FakeHelper = out["helper"]
        bar, card = app.query_one(ControlBar), app.query_one(ConfirmCard)
        await _type(pilot, "/control on")
        await wait_for(lambda: "control on" in bar.render().plain)
        await helper.push_confirm()
        await wait_for(lambda: card.display)
        await helper.stop()
        await wait_for(lambda: not card.display)
        await wait_for(lambda: "control unavailable" in bar.render().plain)
        await pilot.press("y")
        await asyncio.sleep(0.1)
        out["answers"] = list(helper.answers)
        await helper.start()
        await wait_for(lambda: app._control_link.connected and not bar.display, timeout=5.0)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["answers"] == []
    assert any("denied" in note and "jarvis-input" in note for note in out["notes"])
    assert any("Control off" in note for note in out["notes"])


# --- Confirmations from the helper -----------------------------------------------------


def test_helper_confirm_card_n_denies_y_allows_enter_does_nothing(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        helper: FakeHelper = out["helper"]
        card = app.query_one(ConfirmCard)
        await helper.push_confirm(id="c1", title="Press Return in Brave?", message="pc_key Return", timeout_s=30)
        await wait_for(lambda: card.display)
        await pilot.pause()
        out["card"] = card.render().plain
        await pilot.press("f2")
        await pilot.pause()
        out["dev_card"] = card.render().plain if card.display else ""
        await pilot.press("h", "i", "enter")
        await asyncio.sleep(0.1)
        out["after_enter"] = list(helper.answers)
        await pilot.press("n")
        await wait_for(lambda: len(helper.answers) == 1)
        await pilot.pause()
        out["hidden"] = not card.display
        await helper.push_confirm(id="c2")
        await wait_for(lambda: card.display)
        await pilot.press("y")
        await wait_for(lambda: len(helper.answers) == 2)
        await pilot.pause()
        out["focus"] = app.focused is not None and app.focused.id == "prompt"
        out["answers"] = list(helper.answers)

    out = _run(tmp_path, _private_runtime_dir, steps)
    for card in (out["card"], out["dev_card"]):
        assert "Press Return in Brave?" in card and "pc_key Return" in card
        assert "y allow" in card and "n deny" in card and "denied in 30s" in card
    assert out["after_enter"] == []
    assert out["hidden"] and out["focus"]
    assert out["answers"] == [{"op": "answer", "id": "c1", "allow": False},
                              {"op": "answer", "id": "c2", "allow": True}]
    assert _responses(out["daemon"]) == [] and _sends(out["daemon"]) == []


def test_helper_confirm_ignores_keys_until_armed(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await out["helper"].push_confirm()
        await wait_for(lambda: app.query_one(ConfirmCard).display)
        await pilot.press("y")
        await asyncio.sleep(0.1)
        out["still"] = app.query_one(ConfirmCard).display
        out["answers"] = list(out["helper"].answers)

    out = _run(tmp_path, _private_runtime_dir, steps, confirm_arm_s=30.0)
    assert out["still"] is True and out["answers"] == []


def test_helper_confirm_times_out_as_denied(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await out["helper"].push_confirm(timeout_s=0.2)
        await wait_for(lambda: app.query_one(ConfirmCard).display)
        await wait_for(lambda: not app.query_one(ConfirmCard).display, timeout=3.0)
        await wait_for(lambda: bool(out["helper"].answers))
        out["answers"] = list(out["helper"].answers)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["answers"] == [{"op": "answer", "id": "c1", "allow": False}]
    assert any("timed out" in note and "denied" in note for note in out["notes"])


def test_link_drop_clears_the_card(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        helper: FakeHelper = out["helper"]
        await helper.push_confirm()
        await wait_for(lambda: app.query_one(ConfirmCard).display)
        await helper.drop_subscribers()
        await wait_for(lambda: not app.query_one(ConfirmCard).display)
        await pilot.press("y")
        await asyncio.sleep(0.1)
        out["answers"] = list(helper.answers)
        await wait_for(lambda: app._control_link.connected, timeout=5.0)  # it comes back

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["answers"] == []
    assert any("denied" in note for note in out["notes"])


def test_one_card_at_a_time_across_helper_and_daemon(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        card = app.query_one(ConfirmCard)
        await out["helper"].push_confirm(id="c1")
        await wait_for(lambda: card.display)
        await out["daemon"].push(CONFIRM)
        await wait_for(lambda: "+1 waiting" in card.render().plain)
        await pilot.press("y")
        await wait_for(lambda: bool(out["helper"].answers))
        await pilot.pause()
        out["second"] = card.render().plain
        await pilot.press("n")
        await wait_for(lambda: len(_responses(out["daemon"])) == 1)
        out["answers"] = list(out["helper"].answers)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert "Send this message?" in out["second"]
    assert out["answers"] == [{"op": "answer", "id": "c1", "allow": True}]
    assert _responses(out["daemon"]) == [{"type": "ui_response", "requestId": "r1", "confirmed": False}]


def test_helper_confirm_cancels_a_recording(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await pilot.pause()
        assert app._host.voice.recording
        await out["helper"].push_confirm()
        await wait_for(lambda: app.query_one(ConfirmCard).display)
        await pilot.pause()
        out["recording"] = app._host.voice.active
        await pilot.press("escape")  # esc denies
        await wait_for(lambda: bool(out["helper"].answers))
        out["answers"] = list(out["helper"].answers)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["recording"] is False
    assert out["recorders"][0].cancelled
    assert out["answers"] == [{"op": "answer", "id": "c1", "allow": False}]
    assert _sends(out["daemon"]) == []


# --- Confirmations from the daemon (``ask``, other extensions) -----------------------


def test_daemon_confirm_card_n_denies_y_allows(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        daemon: FakeDaemon = out["daemon"]
        card = app.query_one(ConfirmCard)
        assert not card.display
        await daemon.push(CONFIRM)
        await wait_for(lambda: card.display)
        await pilot.pause()
        out["card"] = card.render().plain
        out["footer"] = app.query_one(KeysFooter).render().plain
        await pilot.press("h", "i", "enter")
        await asyncio.sleep(0.1)
        out["after_enter"] = list(_responses(daemon))
        await pilot.press("n")
        await wait_for(lambda: len(_responses(daemon)) == 1)
        await daemon.push({**CONFIRM, "ask": {**CONFIRM["ask"], "requestId": "r2"}})
        await wait_for(lambda: card.display)
        await pilot.press("y")
        await wait_for(lambda: len(_responses(daemon)) == 2)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert "Send this message?" in out["card"] and "pc_key ctrl+enter in Slack" in out["card"]
    assert "y allow" in out["footer"] and "n deny" in out["footer"]
    assert out["after_enter"] == []
    assert _responses(out["daemon"]) == [
        {"type": "ui_response", "requestId": "r1", "confirmed": False},
        {"type": "ui_response", "requestId": "r2", "confirmed": True},
    ]
    assert out["helper"].answers == []
    assert _sends(out["daemon"]) == []


def test_daemon_confirm_times_out_as_denied(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await out["daemon"].push({**CONFIRM, "ask": {**CONFIRM["ask"], "timeoutMs": 200}})
        await wait_for(lambda: app.query_one(ConfirmCard).display)
        await wait_for(lambda: not app.query_one(ConfirmCard).display, timeout=3.0)
        await wait_for(lambda: len(_responses(out["daemon"])) == 1)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert _responses(out["daemon"]) == [{"type": "ui_response", "requestId": "r1", "confirmed": False}]


def test_ask_resolved_elsewhere_clears_the_card(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await out["daemon"].push(CONFIRM)
        await wait_for(lambda: app.query_one(ConfirmCard).display)
        await out["daemon"].push({"type": "ask_resolved", "requestId": "r1"})
        await wait_for(lambda: not app.query_one(ConfirmCard).display)
        await pilot.press("y")
        await asyncio.sleep(0.1)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert _responses(out["daemon"]) == []


def test_daemon_disconnect_clears_only_its_card(tmp_path: Path, _private_runtime_dir: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        card = app.query_one(ConfirmCard)
        await out["daemon"].push(CONFIRM)
        await wait_for(lambda: card.display)
        await out["helper"].push_confirm(id="c1")
        await wait_for(lambda: "+1 waiting" in card.render().plain)
        await out["daemon"].drop()
        await wait_for(lambda: "+1 waiting" not in card.render().plain)
        out["left"] = card.render().plain
        await pilot.press("y")
        await wait_for(lambda: bool(out["helper"].answers))
        out["answers"] = list(out["helper"].answers)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert "Press Return in Brave?" in out["left"]
    assert out["answers"] == [{"op": "answer", "id": "c1", "allow": True}]
    assert _responses(out["daemon"]) == []
    assert any("Gentle Dot disconnected" in note and "denied" in note for note in out["notes"])


@pytest.mark.parametrize("method", ["app", "pin", "select", "input", "editor", "weird"])
def test_other_asks_are_pointed_to_gentle_dot_without_a_card(
    tmp_path: Path, _private_runtime_dir: Path, method: str
) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await out["daemon"].push({"type": "ask", "ask": {"requestId": "r9", "method": method, "title": "Approve?"}})
        await wait_for(lambda: any("Approve?" in n.message for n in app._notifications))
        out["card"] = app.query_one(ConfirmCard).display
        await pilot.press("y")
        await asyncio.sleep(0.1)

    out = _run(tmp_path, _private_runtime_dir, steps)
    assert out["card"] is False
    assert _responses(out["daemon"]) == []
    assert any("Answer this in Gentle Dot" in note for note in out["notes"])
