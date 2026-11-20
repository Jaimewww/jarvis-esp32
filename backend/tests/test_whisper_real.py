"""``WhisperServerTranscriber`` against a REAL running whisper-server. Opt-in.

Skipped unless ``JARVIS_REAL_WHISPER=1``, so CI (which has no server) stays green.
Start the server first (``tools/whisper-server.sh``), then::

    cd backend
    JARVIS_REAL_WHISPER=1 JARVIS_WHISPER_WAV=/path/clip.wav \\
      JARVIS_WHISPER_EXPECT="qué hora" .venv/bin/python -m pytest tests/test_whisper_real.py -v -s -rs

- ``JARVIS_WHISPER_WAV``: WAV PCM 16 kHz mono 16-bit in Spanish. Without it a
  generated 1 s tone is sent and only the round trip is checked (Whisper tends to
  "hear" something like "¡Gracias!" in a tone).
- ``JARVIS_WHISPER_EXPECT``: optional text that must appear in the transcript
  (case-insensitive).
- Server settings come from ``WhisperServerTranscriber.from_env()`` (``JARVIS_STT_URL``…).

Prints the transcript and the stt time (``-s`` shows it).
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.fakes import tone_wav
from app.stt.__main__ import check_format
from app.stt.whisper_server import WhisperServerTranscriber

pytestmark = pytest.mark.skipif(
    os.environ.get("JARVIS_REAL_WHISPER") != "1",
    reason="real whisper-server tests are opt-in: set JARVIS_REAL_WHISPER=1",
)


def test_real_whisper_server_transcribes_a_wav():
    path = os.environ.get("JARVIS_WHISPER_WAV")
    if path:
        with open(path, "rb") as fh:
            wav = fh.read()
    else:
        path, wav = "<generated 1 s tone>", tone_wav(1.0, 440.0)
    seconds = check_format(wav, path)
    stt = WhisperServerTranscriber.from_env()

    timings = []
    for _ in range(2):  # the second run shows the warm time
        started = time.perf_counter()
        text = asyncio.run(stt.transcribe(wav))
        timings.append((time.perf_counter() - started) * 1000)
    print(
        f"\n{path} ({seconds:.1f} s audio) via {stt.url}: "
        f"stt {timings[0]:.0f} ms, then {timings[1]:.0f} ms: {text!r}"
    )
    assert isinstance(text, str)
    expect = os.environ.get("JARVIS_WHISPER_EXPECT")
    if expect:
        assert expect.lower() in text.lower()
