"""Push-to-talk pieces without the window: the recorder process, the whisper client, the model.

No microphone and no whisper-server: the recorder is a fake ``pw-record`` script that
writes a WAV and waits for SIGINT, and the speech server is an in-process HTTP server.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import sys
import threading
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from jarvis import voice
from jarvis.client import AgentStateEvent, ConnectionStatus, MessageDoneEvent, UserMessageEvent
from jarvis.model import HostModel
from jarvis.voice import ProcessRecorder, VoiceError, WhisperTranscriber, recorder_command

# A stand-in recorder: writes a short 16 kHz mono WAV to its last argument, then waits
# for SIGINT (as pw-record does) and exits 0. It records its own arguments next to it.
FAKE_RECORDER = """#!{python}
import signal, sys, time, wave, json, pathlib
out = pathlib.Path(sys.argv[-1])
pathlib.Path(__file__).with_suffix(".args").write_text(json.dumps(sys.argv[1:]))
{early}
def stop(*_):
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\\0\\0" * 1600)
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
while True:
    time.sleep(0.05)
"""


def _fake_tool(directory: Path, name: str, early: str = "") -> Path:
    path = directory / name
    path.write_text(FAKE_RECORDER.format(python=sys.executable, early=early), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _which_in(directory: Path):
    def which(name: str) -> str | None:
        path = directory / name
        return str(path) if path.exists() else None

    return which


# --- recorder ------------------------------------------------------------------


def test_recorder_command_prefers_pw_record_with_16k_mono_s16(tmp_path: Path) -> None:
    out = tmp_path / "a.wav"
    for name in ("pw-record", "parecord", "arecord"):
        _fake_tool(tmp_path, name)
    command = recorder_command(out, _which_in(tmp_path))
    assert command == [str(tmp_path / "pw-record"), "--rate", "16000", "--channels", "1", "--format", "s16", str(out)]


def test_recorder_command_falls_back_to_parecord_then_arecord(tmp_path: Path) -> None:
    out = tmp_path / "a.wav"
    _fake_tool(tmp_path, "arecord")
    command = recorder_command(out, _which_in(tmp_path))
    assert command is not None and command[0].endswith("arecord")
    assert {"16000", "1", "S16_LE", "wav"} <= set(command) and command[-1] == str(out)
    _fake_tool(tmp_path, "parecord")
    command = recorder_command(out, _which_in(tmp_path))
    assert command is not None and command[0].endswith("parecord")
    assert "--rate=16000" in command and "--channels=1" in command and "--format=s16le" in command
    assert recorder_command(out, lambda name: None) is None


def test_no_recorder_is_a_clear_error(tmp_path: Path) -> None:
    recorder = ProcessRecorder(tmp_path / "a.wav", which=lambda name: None)
    with pytest.raises(VoiceError, match="pw-record"):
        asyncio.run(recorder.start())


def test_process_recorder_stops_with_sigint_and_collects_the_wav(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "pw-record")
    out = tmp_path / "rec" / "a.wav"
    out.parent.mkdir()

    async def scenario() -> Path:
        recorder = ProcessRecorder(out, which=_which_in(tmp_path))
        await recorder.start()
        await asyncio.sleep(0.3)  # the script installs its SIGINT handler
        return await recorder.stop()

    path = asyncio.run(scenario())
    assert path == out
    with wave.open(str(path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    args = json.loads((tmp_path / "pw-record.args").read_text())
    assert args == ["--rate", "16000", "--channels", "1", "--format", "s16", str(out)]


def test_process_recorder_cancel_leaves_nothing_running(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "pw-record")

    async def scenario() -> int | None:
        recorder = ProcessRecorder(tmp_path / "a.wav", which=_which_in(tmp_path))
        await recorder.start()
        await recorder.cancel()
        return recorder.returncode

    assert asyncio.run(scenario()) is not None


def test_a_recorder_that_dies_reports_its_own_words(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "pw-record", early='sys.stderr.write("no such device\\n"); sys.exit(1)')

    async def scenario() -> None:
        recorder = ProcessRecorder(tmp_path / "a.wav", which=_which_in(tmp_path))
        await recorder.start()
        await asyncio.sleep(0.3)
        await recorder.stop()

    with pytest.raises(VoiceError, match="no such device"):
        asyncio.run(scenario())


# --- whisper client --------------------------------------------------------------


@contextmanager
def _speech_server(status: int = 200, body: bytes | None = None) -> Iterator[dict[str, Any]]:
    seen: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers["Content-Length"])
            seen["path"] = self.path
            seen["type"] = self.headers["Content-Type"]
            seen["body"] = self.rfile.read(length)
            payload = body if body is not None else json.dumps({"text": " Abre Spotify. "}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    seen["url"] = f"http://127.0.0.1:{server.server_address[1]}/inference"
    try:
        yield seen
    finally:
        server.shutdown()
        server.server_close()


def _wav(tmp_path: Path) -> Path:
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 160)
    return path


def test_transcriber_posts_the_wav_as_multipart_and_reads_the_text(tmp_path: Path) -> None:
    wav = _wav(tmp_path)
    with _speech_server() as seen:
        text = WhisperTranscriber(seen["url"], timeout=5).transcribe(wav)
    assert text == "Abre Spotify."
    assert seen["path"] == "/inference" and seen["type"].startswith("multipart/form-data; boundary=")
    body: bytes = seen["body"]
    assert b'name="file"; filename="a.wav"' in body and wav.read_bytes() in body
    assert b'name="response_format"\r\n\r\njson\r\n' in body
    assert b'name="language"\r\n\r\nes\r\n' in body


def test_transcriber_says_the_status_and_body_of_a_refusal(tmp_path: Path) -> None:
    with _speech_server(status=500, body=b'{"error":"model not loaded"}') as seen:
        with pytest.raises(VoiceError, match=r"HTTP 500.*model not loaded"):
            WhisperTranscriber(seen["url"], timeout=5).transcribe(_wav(tmp_path))


def test_transcriber_says_when_the_server_is_down(tmp_path: Path) -> None:
    with _speech_server() as seen:
        url = seen["url"]
    with pytest.raises(VoiceError, match=r"Can't reach the speech server at http://127\.0\.0\.1:\d+/inference"):
        WhisperTranscriber(url, timeout=2).transcribe(_wav(tmp_path))


def test_transcriber_rejects_an_answer_without_text(tmp_path: Path) -> None:
    with _speech_server(body=b"<html>nope</html>") as seen:
        with pytest.raises(VoiceError, match="no text"):
            WhisperTranscriber(seen["url"], timeout=5).transcribe(_wav(tmp_path))


def test_the_speech_url_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(voice.STT_URL_ENV, raising=False)
    assert WhisperTranscriber().url == "http://127.0.0.1:8080/inference"
    monkeypatch.setenv(voice.STT_URL_ENV, "http://10.0.0.2:9000/inference")
    assert WhisperTranscriber().url == "http://10.0.0.2:9000/inference"


def test_voice_module_does_not_reach_into_the_backend() -> None:
    source = Path(voice.__file__).read_text(encoding="utf-8")
    assert "backend" not in source
    assert "textual" not in source


# --- model -----------------------------------------------------------------------


def test_a_spoken_prompt_is_timed_and_marked_spoken() -> None:
    now = [10.0]
    model = HostModel(monotonic=lambda: now[0])
    model.set_daemon_status(ConnectionStatus.CONNECTED)
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u1", "escrito"))
    model.apply_daemon(MessageDoneEvent("a1", "ok"))
    model.apply_daemon(AgentStateEvent("idle"))
    model.note_sent(spoken=True)
    model.apply_daemon(UserMessageEvent("u2", "dicho"))
    now[0] = 12.5
    model.apply_daemon(MessageDoneEvent("a2", "ok"))
    model.apply_daemon(AgentStateEvent("idle"))
    users = [m for m in model.messages if m.role == "user"]
    assert [m.spoken for m in users] == [False, True]
    assert model.last_timing is not None and model.last_timing.total_s == pytest.approx(2.5)
    assert model.last_timing.spoken


def test_the_user_view_says_said_for_a_spoken_prompt() -> None:
    from jarvis.userview import exchanges, render_rows

    model = HostModel()
    model.set_daemon_status(ConnectionStatus.CONNECTED)
    model.apply_daemon(AgentStateEvent("idle"))
    model.note_sent(spoken=True)
    model.apply_daemon(UserMessageEvent("u1", "abre spotify"))
    model.apply_daemon(MessageDoneEvent("a1", "Listo."))
    model.note_sent()
    model.apply_daemon(UserMessageEvent("u2", "gracias"))
    model.apply_daemon(MessageDoneEvent("a2", "De nada."))
    assert [e.source for e in exchanges(model)] == ["voice", "typed"]
    text = ["".join(t for t, _ in row) for row in render_rows(model, 120, 40)]
    said = next(line for line in text if "abre spotify" in line)
    typed = next(line for line in text if "gracias" in line)
    assert re.search(r"said\s+◎", said) and re.search(r"typed\s+▸", typed)


def test_the_developer_view_marks_a_spoken_prompt() -> None:
    from jarvis.devview import render_rows

    model = HostModel()
    model.set_daemon_status(ConnectionStatus.CONNECTED)
    model.note_sent(spoken=True)
    model.apply_daemon(UserMessageEvent("u1", "abre spotify"))
    model.apply_daemon(MessageDoneEvent("a1", "Listo."))
    text = ["".join(t for t, _ in row) for row in render_rows(model, 140, 40)]
    line = next(line for line in text if "abre spotify" in line)
    assert "◎" in line.split("abre spotify")[0]


@pytest.mark.skipif(os.environ.get("JARVIS_REAL_WHISPER") != "1", reason="set JARVIS_REAL_WHISPER=1")
def test_real_whisper_server_transcribes_a_wav() -> None:
    """Opt-in: ``JARVIS_REAL_WHISPER=1 JARVIS_WHISPER_WAV=~/clip.wav`` against the local server."""

    wav = Path(os.environ.get("JARVIS_WHISPER_WAV", "")).expanduser()
    if not wav.is_file():
        pytest.skip("set JARVIS_WHISPER_WAV to an existing WAV")
    text = WhisperTranscriber().transcribe(wav)
    print(f"whisper heard: {text!r}")
    assert text.strip()
