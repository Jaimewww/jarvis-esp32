"""Test doubles for the stage interfaces in ``app.ports`` (docs/contratos.md).

Fixed, configurable outputs and a record of the calls received, so the backend
and its tests can run without real STT, agent or TTS.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import AsyncIterator, Sequence

SAMPLE_RATE = 16000


def tone_wav(seconds: float = 0.4, freq: float = 1000.0) -> bytes:
    """A sine tone as WAV PCM 16 kHz mono 16-bit."""
    total = int(SAMPLE_RATE * seconds)
    frames = bytearray()
    for i in range(total):
        sample = int(8000 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
        frames += struct.pack("<h", sample)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


class FakeTranscriber:
    def __init__(self, text: str = "qué hora es") -> None:
        self.text = text
        self.calls: list[int] = []  # size in bytes of each clip received

    async def transcribe(self, wav: bytes) -> str:
        self.calls.append(len(wav))
        return self.text


class FakeAgent:
    def __init__(self, chunks: Sequence[str] = ("En orden, señor.",)) -> None:
        self.chunks = list(chunks)
        self.calls: list[tuple[str, str]] = []  # (text, turn_id)
        self.aborts = 0
        self.resets = 0

    async def reply(self, text: str, *, turn_id: str) -> AsyncIterator[str]:
        self.calls.append((text, turn_id))
        for chunk in self.chunks:
            yield chunk

    async def abort(self) -> None:
        self.aborts += 1

    async def reset(self) -> None:
        self.resets += 1


class FakeSynthesizer:
    def __init__(self, wav: bytes | None = None) -> None:
        self.wav = wav if wav is not None else tone_wav()
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return self.wav
