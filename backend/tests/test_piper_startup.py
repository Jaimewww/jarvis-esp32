"""Piper runs as a bounded child process: clear errors, timeouts, no beep fallback."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import textwrap
import time
import wave
from pathlib import Path

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app import main
from app.tts import FFMPEG_TIMEOUT_S, INSTALL_HINT, PIPER_TIMEOUT_S, PiperSynthesizer

REPLY = "En orden, señor."


def _script(path: Path, first_line: str, body: str) -> Path:
    path.write_text(first_line + "\n" + textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def model(tmp_path) -> Path:
    model_file = tmp_path / "voice.onnx"
    model_file.write_bytes(b"onnx")
    return model_file


@pytest.fixture
def stub_piper(tmp_path) -> Path:
    """An interpreter stand-in that writes the WAV the real Piper CLI would write."""
    return _script(
        tmp_path / "stub-piper",
        f"#!{sys.executable}",
        """
        import os
        import sys
        import wave
        from pathlib import Path

        args = sys.argv[1:]
        output = Path(args[args.index("-f") + 1])
        text = args[args.index("--") + 1]
        marker = os.environ.get("JARVIS_STUB_TEXT")
        if marker:
            Path(marker).write_text(text, encoding="utf-8")
        with wave.open(str(output), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\\x01\\x00" * 1600)
        """,
    )


@pytest.fixture
def stub_ffmpeg(tmp_path) -> Path:
    """An ``ffmpeg`` stand-in that copies the input WAV to the output path."""
    return _script(
        tmp_path / "ffmpeg",
        "#!/bin/sh",
        """
        source=""
        target=""
        while [ $# -gt 0 ]; do
          case "$1" in
            -i) source=$2; shift 2 ;;
            *) target=$1; shift ;;
          esac
        done
        cp -- "$source" "$target"
        """,
    )


def _stub_path(monkeypatch, directory: Path) -> None:
    monkeypatch.setenv("PATH", f"{directory}:{os.environ['PATH']}")


def _speak(synthesizer: PiperSynthesizer) -> bytes:
    return asyncio.run(synthesizer.synthesize(REPLY))


def test_missing_model_is_reported_before_starting_anything(tmp_path):
    absent = tmp_path / "absent.onnx"
    with pytest.raises(FileNotFoundError) as caught:
        PiperSynthesizer(absent)
    assert str(caught.value) == f"JARVIS_PIPER_MODEL does not point to a file: {absent}"


def test_missing_piper_module_is_reported(model, tmp_path):
    no_piper = _script(
        tmp_path / "no-piper",
        f"#!{sys.executable}",
        """
        import sys

        sys.stderr.write("No module named piper\\n")
        sys.exit(1)
        """,
    )
    with pytest.raises(RuntimeError) as caught:
        _speak(PiperSynthesizer(model, python=str(no_piper)))
    assert str(caught.value) == INSTALL_HINT


def test_piper_failure_reports_exit_status_and_stderr(model, tmp_path):
    broken = _script(
        tmp_path / "broken-piper",
        f"#!{sys.executable}",
        """
        import sys

        sys.stderr.write("piper exploded\\n")
        sys.exit(2)
        """,
    )
    with pytest.raises(RuntimeError) as caught:
        _speak(PiperSynthesizer(model, python=str(broken)))
    assert str(caught.value) == "Piper failed (exit 2): piper exploded"


def test_piper_timeout_kills_and_reaps_the_child(model, tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("JARVIS_STUB_PID", str(pid_file))
    sleeper = _script(
        tmp_path / "slow-piper",
        "#!/bin/sh",
        """
        echo $$ > "$JARVIS_STUB_PID"
        sleep 30
        """,
    )
    with pytest.raises(RuntimeError) as caught:
        _speak(PiperSynthesizer(model, python=str(sleeper), timeout=0.2))
    assert str(caught.value) == "Piper did not finish within 0.2 s"
    child = int(pid_file.read_text().strip())
    assert not _still_running(child), "the timed-out Piper child was left running"


def test_missing_ffmpeg_is_reported(model, stub_piper, tmp_path, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(RuntimeError) as caught:
        _speak(PiperSynthesizer(model, python=str(stub_piper)))
    assert "ffmpeg" in str(caught.value)
    assert "PATH" in str(caught.value)


def test_ffmpeg_timeout_is_bounded(model, stub_piper, tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("JARVIS_STUB_PID", str(pid_file))
    slow_ffmpeg = _script(
        tmp_path / "slow-ffmpeg",
        "#!/bin/sh",
        """
        echo $$ > "$JARVIS_STUB_PID"
        sleep 30
        """,
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ffmpeg").write_text(slow_ffmpeg.read_text(encoding="utf-8"), encoding="utf-8")
    (bin_dir / "ffmpeg").chmod(0o755)
    _stub_path(monkeypatch, bin_dir)
    with pytest.raises(RuntimeError) as caught:
        _speak(PiperSynthesizer(model, python=str(stub_piper), ffmpeg_timeout=0.2))
    assert str(caught.value) == "FFmpeg did not finish within 0.2 s"
    child = int(pid_file.read_text().strip())
    assert not _still_running(child), "the timed-out FFmpeg child was left running"


def test_the_child_speaks_the_exact_reply(model, stub_piper, stub_ffmpeg, tmp_path, monkeypatch):
    text_file = tmp_path / "text"
    monkeypatch.setenv("JARVIS_STUB_TEXT", str(text_file))
    _stub_path(monkeypatch, tmp_path)
    audio = _speak(PiperSynthesizer(model, python=str(stub_piper)))
    assert text_file.read_text(encoding="utf-8") == REPLY
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.getnframes() == 1600


@pytest.fixture
def isolated_audio(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_PIPER_MODEL", raising=False)
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "REPLY_WAV", tmp_path / "jarvis-reply.wav")
    mount = next(route for route in main.app.routes if isinstance(route, Mount) and route.name == "static")
    monkeypatch.setattr(mount, "app", StaticFiles(directory=tmp_path))


def test_configured_startup_failure_does_not_fall_back_to_the_beep(isolated_audio, monkeypatch, tmp_path):
    class FailingSynthesizer:
        async def synthesize(self, text):
            raise RuntimeError("piper down")

    monkeypatch.setenv("JARVIS_PIPER_MODEL", str(tmp_path / "voice.onnx"))
    monkeypatch.setattr(main, "make_synthesizer", lambda model: FailingSynthesizer())
    with pytest.raises(RuntimeError, match="piper down"), TestClient(main.app):
        pass
    assert not (tmp_path / "jarvis-spoken-reply.wav").exists()
    assert not (tmp_path / "jarvis-reply.wav").exists()


def test_configured_startup_reports_a_missing_model_instead_of_the_beep(isolated_audio, tmp_path, monkeypatch):
    absent = tmp_path / "absent.onnx"
    monkeypatch.setenv("JARVIS_PIPER_MODEL", str(absent))
    with pytest.raises(FileNotFoundError, match="does not point to a file"), TestClient(main.app):
        pass
    assert not (tmp_path / "jarvis-spoken-reply.wav").exists()
    assert not (tmp_path / "jarvis-reply.wav").exists()


def test_timeouts_are_hard_and_bounded():
    assert isinstance(PIPER_TIMEOUT_S, float) and PIPER_TIMEOUT_S > 0
    assert isinstance(FFMPEG_TIMEOUT_S, float) and FFMPEG_TIMEOUT_S > 0


def _still_running(pid: int, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.05)
    return True
