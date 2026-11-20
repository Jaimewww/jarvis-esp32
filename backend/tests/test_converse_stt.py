"""The ``stt`` stage of ``/converse``: transcriber selection, ``turn_stage`` and failures."""

from __future__ import annotations

import json

import httpx
import pytest

from app import main
from app.fakes import FakeTranscriber
from app.stt import make_transcriber
from app.stt.whisper_server import WhisperServerTranscriber
from conftest import DEVICE_HEADERS

URL = "http://whisper.test:8080/inference"


def _turn_events(client, wav):
    """POST one turn and return (response, events broadcast during it)."""
    with client.websocket_connect(f"/api/v1/ws?token={main.HOST_TOKEN}") as ws:
        assert json.loads(ws.receive_bytes())["type"] == "esp32_snapshot"
        resp = client.post(
            "/api/v1/converse",
            headers=DEVICE_HEADERS,
            files={"audio": ("clip.wav", wav, "audio/wav")},
        )
        # Sentinel heartbeat: see test_converse.py.
        client.post(
            "/api/v1/heartbeat",
            headers=DEVICE_HEADERS,
            json={"device_id": "sentinel", "state": "idle"},
        )
        events = []
        while True:
            event = json.loads(ws.receive_bytes())
            if event["type"] == "esp32_state" and event["device_id"] == "sentinel":
                break
            events.append(event)
    return resp, events


def _whisper(handler) -> WhisperServerTranscriber:
    return WhisperServerTranscriber(URL, transport=httpx.MockTransport(handler))


def test_default_transcriber_is_the_fake(monkeypatch):
    monkeypatch.delenv("JARVIS_STT", raising=False)
    assert isinstance(make_transcriber(), FakeTranscriber)


def test_whisper_env_selects_the_whisper_server(monkeypatch):
    monkeypatch.setenv("JARVIS_STT", "whisper")
    monkeypatch.setenv("JARVIS_STT_URL", URL)
    t = make_transcriber()
    assert isinstance(t, WhisperServerTranscriber) and t.url == URL


def test_converse_transcribes_with_whisper_and_reports_stt_stage(client, wav, monkeypatch):
    received: list[bytes] = []

    def handler(request):
        received.append(request.read())
        return httpx.Response(200, json={"text": " enciende la luz "})

    monkeypatch.setattr(main, "transcriber", _whisper(handler))
    resp, events = _turn_events(client, wav)
    assert resp.status_code == 200
    assert len(received) == 1 and wav in received[0]
    types = [e["type"] for e in events]
    assert types.index("turn_started") < types.index("turn_stage") < types.index("turn_done")
    stage = events[types.index("turn_stage")]
    assert stage["stage"] == "stt"
    assert stage["text"] == "enciende la luz"
    assert stage["turn_id"] == resp.json()["turn_id"]
    assert isinstance(stage["ms"], (int, float)) and stage["ms"] >= 0
    assert isinstance(stage["ts"], float)
    done = events[types.index("turn_done")]
    assert done["total_ms"] >= stage["ms"]
    assert "turn_error" not in types
    # The rest of the turn is unchanged: canned reply and beep.
    assert resp.json()["reply_text"] == done["reply_text"]


def test_converse_default_fake_also_reports_stt_stage(client, wav, monkeypatch):
    fake = FakeTranscriber("qué hora es")
    monkeypatch.setattr(main, "transcriber", fake)
    resp, events = _turn_events(client, wav)
    assert resp.status_code == 200
    assert fake.calls == [len(wav)]
    stages = [e for e in events if e["type"] == "turn_stage"]
    assert [(s["stage"], s["text"]) for s in stages] == [("stt", "qué hora es")]


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500, text="boom"),
        lambda request: httpx.Response(200, text="not json"),
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)),
    ],
    ids=["http-500", "bad-json", "timeout", "server-down"],
)
def test_converse_stt_failure_ends_turn_with_turn_error(client, wav, monkeypatch, handler):
    monkeypatch.setattr(main, "transcriber", _whisper(handler))
    resp, events = _turn_events(client, wav)
    assert resp.status_code == 502
    assert URL in resp.json()["detail"]
    types = [e["type"] for e in events]
    assert "turn_done" not in types and "action" not in types
    assert types.count("turn_error") == 1
    error = events[types.index("turn_error")]
    assert error["stage"] == "stt" and URL in error["message"]
    assert error["turn_id"] == events[types.index("turn_started")]["turn_id"]
