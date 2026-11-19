from conftest import DEVICE_HEADERS


def _beat(client, state):
    return client.post(
        "/api/v1/heartbeat",
        headers=DEVICE_HEADERS,
        json={"device_id": "esp32-s3", "state": state, "rssi": -50, "ip": "10.0.0.2"},
    )


def test_heartbeat_accepts_error_state(client):
    resp = _beat(client, "error")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert client.get("/api/v1/health").json()["devices"]["esp32-s3"]["state"] == "error"


def test_heartbeat_rejects_unknown_state(client):
    resp = _beat(client, "dancing")
    assert resp.status_code == 422
