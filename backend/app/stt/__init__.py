"""Transcriber implementations behind ``app.ports.Transcriber`` (contract C3).

``JARVIS_STT`` picks the one ``/converse`` uses:

- ``fake`` (default, so CI and tests keep the double): ``app.fakes.FakeTranscriber``.
- ``whisper``: ``WhisperServerTranscriber`` against a local whisper.cpp server
  (``JARVIS_STT_URL``, ``JARVIS_STT_LANGUAGE``, ``JARVIS_STT_TIMEOUT_S``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from app.fakes import FakeTranscriber
from app.ports import Transcriber
from app.stt.whisper_server import WhisperServerTranscriber

ENV_STT = "JARVIS_STT"
CHOICES = ("fake", "whisper")


def make_transcriber(environ: Mapping[str, str] | None = None) -> Transcriber:
    env = os.environ if environ is None else environ
    choice = (env.get(ENV_STT) or "fake").strip().lower()
    if choice == "fake":
        return FakeTranscriber()
    if choice == "whisper":
        return WhisperServerTranscriber.from_env(env)
    raise ValueError(f"{ENV_STT}={choice!r} is not one of {', '.join(CHOICES)}")
