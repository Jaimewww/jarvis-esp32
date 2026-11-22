"""Optional local Piper implementation of the Synthesizer port."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from app.ports import Synthesizer

PIPER_TIMEOUT_S = 30.0
FFMPEG_TIMEOUT_S = 30.0
INSTALL_HINT = "Install requirements-voice.txt to use JARVIS_PIPER_MODEL"
FFMPEG_HINT = "Install ffmpeg and make it available on PATH to use JARVIS_PIPER_MODEL"


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "no output").strip()


class PiperSynthesizer:
    """Speaks the reply through the Piper CLI in a bounded child process.

    Loading the voice inside this process blocks the event loop at startup and lets a
    native failure take the backend down, so the CLI child gets the work: a hard
    timeout kills and reaps it, and any failure raises instead of silently falling
    back to the demo beep.
    """

    def __init__(
        self,
        model: Path,
        *,
        python: str = sys.executable,
        timeout: float = PIPER_TIMEOUT_S,
        ffmpeg_timeout: float = FFMPEG_TIMEOUT_S,
    ) -> None:
        if not model.is_file():
            raise FileNotFoundError(f"JARVIS_PIPER_MODEL does not point to a file: {model}")
        self._model = model
        self._python = python
        self._timeout = timeout
        self._ffmpeg_timeout = ffmpeg_timeout

    async def synthesize(self, text: str) -> bytes:
        return await asyncio.to_thread(self._synthesize, text)

    def _synthesize(self, text: str) -> bytes:
        with TemporaryDirectory(prefix="jarvis-piper-") as directory:
            source = Path(directory) / "piper.wav"
            target = Path(directory) / "reply.wav"
            self._run_piper(text, source)
            self._run_ffmpeg(source, target)
            return target.read_bytes()

    def _run_piper(self, text: str, source: Path) -> None:
        command = [self._python, "-m", "piper", "-m", str(self._model), "-f", str(source), "--", text]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=self._timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Piper did not finish within {self._timeout:g} s") from None
        except FileNotFoundError:
            raise RuntimeError(f"Piper cannot start: {self._python} was not found") from None
        if result.returncode != 0:
            detail = _detail(result)
            if "No module named piper" in detail:
                raise RuntimeError(INSTALL_HINT)
            raise RuntimeError(f"Piper failed (exit {result.returncode}): {detail}")

    def _run_ffmpeg(self, source: Path, target: Path) -> None:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(target),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=self._ffmpeg_timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"FFmpeg did not finish within {self._ffmpeg_timeout:g} s") from None
        except FileNotFoundError:
            raise RuntimeError(FFMPEG_HINT) from None
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed (exit {result.returncode}): {_detail(result)}")


def make_synthesizer(model: Path) -> Synthesizer:
    return PiperSynthesizer(model)
