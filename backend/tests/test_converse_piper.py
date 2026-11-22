"""The locally configured voice serves the fixed reply without changing C1."""

import io
import os
import wave
from pathlib import Path

import pytest
from conftest import DEVICE_HEADERS
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app import main

REPLY = "En orden, señor."


@pytest.fixture
def isolated_audio(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_PIPER_MODEL", raising=False)
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "REPLY_WAV", tmp_path / "jarvis-reply.wav")
    mount = next(route for route in main.app.routes if isinstance(route, Mount) and route.name == "static")
    monkeypatch.setattr(mount, "app", StaticFiles(directory=tmp_path))


def _converse(client, wav):
    response = client.post(
        "/api/v1/converse",
        headers=DEVICE_HEADERS,
        files={"audio": ("clip.wav", wav, "audio/wav")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "turn_id": body["turn_id"],
        "reply_text": REPLY,
        "reply_audio_url": body["reply_audio_url"],
        "actions": [{"type": "media_key", "value": "play_pause"}],
    }
    assert isinstance(body["turn_id"], str) and body["turn_id"]
    url = body["reply_audio_url"]
    audio = client.get(url)
    assert audio.status_code == 200
    return url, audio.content


def test_without_piper_keeps_the_demo_reply(isolated_audio, wav):
    with TestClient(main.app) as client:
        url, data = _converse(client, wav)
    assert url == "/static/jarvis-reply.wav"
    with wave.open(io.BytesIO(data), "rb") as audio:
        assert audio.getnframes() == 6400  # existing 400 ms test beep


def test_configured_synthesizer_speaks_the_default_reply_once(isolated_audio, monkeypatch, wav):
    calls = []

    class RecordingSynthesizer:
        async def synthesize(self, text):
            calls.append(text)
            return wav

    def make_synthesizer(model):
        assert Path(model) == Path("voice.onnx")
        return RecordingSynthesizer()

    monkeypatch.setenv("JARVIS_PIPER_MODEL", "voice.onnx")
    monkeypatch.setattr(main, "make_synthesizer", make_synthesizer, raising=False)
    with TestClient(main.app) as client:
        first = _converse(client, wav)
        second = _converse(client, wav)
    assert first == second == ("/static/jarvis-spoken-reply.wav", wav)
    assert calls == [REPLY]


def test_real_local_piper_wav_from_converse(isolated_audio, monkeypatch, wav):
    model = os.environ.get("JARVIS_TEST_PIPER_MODEL")
    if not model:
        pytest.skip("set JARVIS_TEST_PIPER_MODEL to a local Piper voice")
    monkeypatch.setenv("JARVIS_PIPER_MODEL", model)
    with TestClient(main.app) as client:
        url, data = _converse(client, wav)
    assert url == "/static/jarvis-spoken-reply.wav"
    with wave.open(io.BytesIO(data), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        assert any(audio.readframes(audio.getnframes()))
