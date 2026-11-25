"""Push-to-talk in the window: f4 records, f4 again transcribes and sends as spoken.

The recorder and the transcriber are fakes; no microphone, no whisper-server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from conftest import fake_server, wait_for
from fake_gentle_dot import FakeDaemon

from jarvis.devview import DeveloperView
from jarvis.tui import JarvisApp, KeysFooter, VoiceBar
from jarvis.userview import UserView
from jarvis.voice import VoiceError


class FakeRecorder:
    def __init__(self, path: Path, *, start_error: str | None = None) -> None:
        self.path = path
        self.start_error = start_error
        self.started = self.stopped = self.cancelled = False

    async def start(self) -> None:
        if self.start_error:
            raise VoiceError(self.start_error)
        self.started = True

    async def stop(self) -> Path:
        self.stopped = True
        self.path.write_bytes(b"RIFF fake wav")
        return self.path

    async def cancel(self) -> None:
        self.cancelled = True


class FakeTranscriber:
    def __init__(self, text: str = "abre spotify", error: str | None = None, delay: float = 0.0) -> None:
        self.text, self.error, self.delay = text, error, delay
        self.paths: list[Path] = []
        self.existed: list[bool] = []

    def transcribe(self, path: Path) -> str:
        import time

        self.paths.append(path)
        self.existed.append(path.exists())
        time.sleep(self.delay)
        if self.error:
            raise VoiceError(self.error)
        return self.text


async def _idle_backend(socket: Any) -> None:
    await socket.wait_closed()


Steps = Callable[[Any, JarvisApp, dict[str, Any]], Awaitable[None]]


def _run(
    tmp_path: Path,
    steps: Steps,
    *,
    transcriber: FakeTranscriber | None = None,
    start_error: str | None = None,
    daemon: FakeDaemon | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    token = tmp_path / "token"
    token.write_text("t\n", encoding="utf-8")
    daemon = daemon or FakeDaemon()
    out: dict[str, Any] = {"recorders": [], "daemon": daemon, "transcriber": transcriber or FakeTranscriber()}

    def factory(path: Path) -> FakeRecorder:
        recorder = FakeRecorder(path, start_error=start_error)
        out["recorders"].append(recorder)
        return recorder

    async def scenario() -> None:
        async with fake_server(_idle_backend) as port, fake_server(daemon.handler) as daemon_port:
            app = JarvisApp(
                url=f"ws://127.0.0.1:{daemon_port}/ws",
                token_path=token,
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
                recorder_factory=factory,
                transcriber=out["transcriber"],
                **kwargs,
            )
            async with app.run_test(size=(120, 36)) as pilot:
                await wait_for(lambda: app._host.agent_thinking is not None, timeout=5.0)
                await steps(pilot, app, out)
                out["notes"] = [n.message for n in app._notifications]

    asyncio.run(scenario())
    return out


def _sends(daemon: FakeDaemon) -> list[dict[str, Any]]:
    return [m for m in daemon.received if m["type"] == "send"]


def test_f4_twice_records_transcribes_and_sends_as_spoken(tmp_path: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        footer = app.query_one(KeysFooter).render().plain
        assert "f4 talk" in footer
        await pilot.press("f4")
        await pilot.pause()
        recorder = out["recorders"][0]
        assert recorder.started and app._host.voice.recording
        out["temp"] = recorder.path.parent
        assert (recorder.path.parent.stat().st_mode & 0o777) == 0o700  # private temp dir
        bar = app.query_one(VoiceBar)
        assert bar.display and "● recording" in bar.render().plain and "f4 to send" in bar.render().plain
        assert "esc cancels" in bar.render().plain
        assert "f4 send" in app.query_one(KeysFooter).render().plain
        await pilot.press("f2")  # the bar is shared: the developer view shows it too
        await pilot.pause()
        assert bar.display and "recording" in bar.render().plain
        await pilot.press("f2")
        await pilot.press("f4")
        await wait_for(lambda: app._host.last_timing is not None and app._host.last_timing.settled, timeout=5.0)
        await pilot.pause()
        out["user"] = app.query_one(UserView).render().plain
        out["bar"] = bar.display
        await pilot.press("f2")
        await pilot.pause()
        out["developer"] = app.query_one(DeveloperView).render().plain

    out = _run(tmp_path, steps)
    assert _sends(out["daemon"]) == [{"type": "send", "text": "abre spotify"}]
    assert out["transcriber"].existed == [True]
    assert not out["temp"].exists()  # the recording is gone once used
    line = next(line for line in out["user"].splitlines() if "abre spotify" in line)
    assert "said" in line and "◎" in line and "typed" not in line
    dev = next(line for line in out["developer"].splitlines() if "abre spotify" in line)
    assert "◎" in dev
    assert out["bar"] is False


def test_esc_cancels_a_recording_without_sending(tmp_path: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await pilot.pause()
        out["temp"] = out["recorders"][0].path.parent
        await pilot.press("escape")
        await pilot.pause()
        await asyncio.sleep(0.2)
        out["recording"] = app._host.voice.active
        out["bar"] = app.query_one(VoiceBar).display

    out = _run(tmp_path, steps)
    assert out["recorders"][0].cancelled and not out["recorders"][0].stopped
    assert out["transcriber"].paths == [] and _sends(out["daemon"]) == []
    assert not out["temp"].exists()
    assert out["recording"] is False and out["bar"] is False
    assert any("cancelled" in note for note in out["notes"])


def test_nothing_heard_sends_nothing(tmp_path: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await pilot.pause()
        await pilot.press("f4")
        await wait_for(lambda: not app._host.voice.active, timeout=5.0)
        await asyncio.sleep(0.2)

    out = _run(tmp_path, steps, transcriber=FakeTranscriber(text="  \n "))
    assert _sends(out["daemon"]) == []
    assert any("Nothing was heard" in note for note in out["notes"])


def test_a_transcription_error_is_shown_with_its_cause(tmp_path: Path) -> None:
    cause = "Can't reach the speech server at http://127.0.0.1:8080/inference: connection refused"

    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await pilot.pause()
        out["temp"] = out["recorders"][0].path.parent
        await pilot.press("f4")
        await wait_for(lambda: not app._host.voice.active, timeout=5.0)
        await pilot.pause()

    out = _run(tmp_path, steps, transcriber=FakeTranscriber(error=cause))
    assert _sends(out["daemon"]) == []
    assert any(cause in note for note in out["notes"])
    assert not out["temp"].exists()


def test_no_recorder_is_said_and_nothing_starts(tmp_path: Path) -> None:
    cause = "No recorder found: install pw-record (PipeWire), parecord or arecord."

    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await pilot.pause()
        out["active"] = app._host.voice.active

    out = _run(tmp_path, steps, start_error=cause)
    assert out["active"] is False
    assert any(cause in note for note in out["notes"])


def test_the_recording_stops_by_itself_at_the_cap(tmp_path: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await wait_for(lambda: bool(_sends(out["daemon"])), timeout=5.0)

    out = _run(tmp_path, steps, max_recording_s=0.3)
    assert out["recorders"][0].stopped
    assert _sends(out["daemon"]) == [{"type": "send", "text": "abre spotify"}]


def test_the_transcribing_state_is_shown(tmp_path: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await pilot.pause()
        await pilot.press("f4")
        await wait_for(lambda: app._host.voice.transcribing, timeout=5.0)
        await pilot.pause()
        out["bar"] = app.query_one(VoiceBar).render().plain
        await wait_for(lambda: bool(_sends(out["daemon"])), timeout=5.0)

    out = _run(tmp_path, steps, transcriber=FakeTranscriber(delay=0.5))
    assert "transcribing" in out["bar"]


def test_no_recording_while_signing_in(tmp_path: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.click("#prompt")
        await pilot.press(*"/login", "enter")
        await wait_for(lambda: app._host.login.open, timeout=5.0)
        await pilot.press("f4")
        await pilot.pause()
        out["active"] = app._host.voice.active
        out["footer"] = app.query_one(KeysFooter).render().plain

    out = _run(tmp_path, steps)
    assert out["recorders"] == [] and out["active"] is False
    assert "f4" not in out["footer"]
    assert any("sign-in" in note for note in out["notes"])


def test_opening_login_while_recording_drops_the_recording(tmp_path: Path) -> None:
    async def steps(pilot: Any, app: JarvisApp, out: dict[str, Any]) -> None:
        await pilot.press("f4")
        await pilot.pause()
        await pilot.click("#prompt")
        await pilot.press(*"/login", "enter")
        await wait_for(lambda: app._host.login.open, timeout=5.0)
        await asyncio.sleep(0.2)
        out["active"] = app._host.voice.active

    out = _run(tmp_path, steps)
    assert out["recorders"][0].cancelled and out["active"] is False
    assert out["transcriber"].paths == [] and _sends(out["daemon"]) == []


def test_f4_is_in_the_footer_and_gives_way_to_the_developer_legend() -> None:
    from jarvis.devview import LEGEND, footer_row
    from jarvis.model import Login
    from jarvis.tui import footer_keys

    keys = footer_keys(Login())
    assert ("f4", "talk") in keys

    def text(w: int, legend: bool) -> str:
        return "".join(t for t, _ in footer_row(w, keys, legend, ("f4",)))

    assert "f4 talk" in text(80, False) and "f4 talk" in text(140, False)
    dev = text(140, True)
    assert "f4 talk" not in dev and all(word in dev for _, word in LEGEND)  # the legend stays whole
    assert "f4 talk" in text(170, True)


def test_the_voice_row_fits_every_width_and_uses_theme_classes() -> None:
    from jarvis.model import VoiceInput
    from jarvis.theme import BACKGROUND, FOREGROUND
    from jarvis.tui import voice_row

    now = [5.0]
    voice = VoiceInput(lambda: now[0])
    voice.start()
    now[0] = 8.24
    for w in (30, 60, 120):
        row = voice_row(voice, w)
        assert sum(len(t) for t, _ in row) == w
    text = "".join(t for t, _ in voice_row(voice, 120))
    assert "● recording 3.2s  ·  f4 to send  ·  esc cancels" in text
    classes = {c for _, cls in voice_row(voice, 120) for c in cls.split()}
    assert classes <= set(FOREGROUND) | set(BACKGROUND) | {"b"}
    voice.transcribe()
    assert "◐ transcribing…" in "".join(t for t, _ in voice_row(voice, 120))
