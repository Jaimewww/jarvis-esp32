import json

from app import main
from conftest import DEVICE_HEADERS


def _converse(client, wav):
    return client.post(
        "/api/v1/converse",
        headers=DEVICE_HEADERS,
        files={"audio": ("clip.wav", wav, "audio/wav")},
    )


def test_converse_returns_turn_id_and_previous_fields(client, wav):
    resp = _converse(client, wav)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["turn_id"], str) and body["turn_id"]
    assert isinstance(body["reply_text"], str) and body["reply_text"]
    assert body["reply_audio_url"].startswith("/static/")
    assert body["actions"] == [{"type": "media_key", "value": "play_pause"}]


def test_ws_receives_turn_started_then_turn_done(client, wav):
    url = f"/api/v1/ws?token={main.HOST_TOKEN}"
    with client.websocket_connect(url) as ws:
        assert json.loads(ws.receive_bytes())["type"] == "esp32_snapshot"
        body = _converse(client, wav).json()
        # A heartbeat after the turn is a sentinel: everything the turn
        # broadcast is already queued before its esp32_state arrives, so the
        # test never blocks waiting for an event that was not sent.
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
    types = [e["type"] for e in events]
    assert "turn_started" in types and "turn_done" in types
    assert types.index("turn_started") < types.index("turn_done")
    started = events[types.index("turn_started")]
    done = events[types.index("turn_done")]
    assert started["turn_id"] == done["turn_id"] == body["turn_id"]
    assert started["device_id"] == "esp32-s3"
    assert done["lane"] == "chat"
    assert done["reply_text"] == body["reply_text"]
    assert isinstance(done["total_ms"], (int, float)) and done["total_ms"] >= 0
    assert isinstance(started["ts"], float) and isinstance(done["ts"], float)
