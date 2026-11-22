"""Optional local Piper implementation of the Synthesizer port."""

import asyncio
import subprocess
import wave
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

from app.ports import Synthesizer


class PiperSynthesizer:
    def __init__(self, model: Path) -> None:
        if not model.is_file():
            raise FileNotFoundError(f"JARVIS_PIPER_MODEL does not point to a file: {model}")
        try:
            piper = import_module("piper")
        except ModuleNotFoundError as error:
            if error.name != "piper":
                raise
            raise RuntimeError("Install requirements-voice.txt to use JARVIS_PIPER_MODEL") from error
        self._voice = piper.PiperVoice.load(model)

    async def synthesize(self, text: str) -> bytes:
        return await asyncio.to_thread(self._synthesize, text)

    def _synthesize(self, text: str) -> bytes:
        with TemporaryDirectory(prefix="jarvis-piper-") as directory:
            source = Path(directory) / "piper.wav"
            target = Path(directory) / "reply.wav"
            with wave.open(str(source), "wb") as audio:
                self._voice.synthesize_wav(text, audio)
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                    str(target),
                ],
                check=True,
                timeout=30,
            )
            return target.read_bytes()


def make_synthesizer(model: Path) -> Synthesizer:
    return PiperSynthesizer(model)
