"""Shared fixtures: a TestClient over the real app with uploads in a temp dir."""

from __future__ import annotations

import io
import math
import struct
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main  # noqa: E402

DEVICE_HEADERS = {"X-Device-Token": main.DEVICE_TOKEN}


def make_wav(seconds: float = 0.2) -> bytes:
    rate = 16000
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(rate * seconds))
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)
    main.devices.clear()
    with TestClient(main.app) as c:
        yield c
    main.devices.clear()


@pytest.fixture
def wav() -> bytes:
    return make_wav()
