"""JARVIS orchestrator v0 skeleton — runs on the user's PC.

Implements the v0 transport contract defined on 2026-11-16 (docs/arquitectura.md,
docs/nube.md):

- POST /api/v1/converse  : ESP32 uploads a WAV clip; returns a turn id, reply
                           text, a reply audio URL and the PC actions, and
                           broadcasts ``turn_started`` / ``turn_done``.
- POST /api/v1/heartbeat : ESP32 keepalive with device state; relayed to host
                           clients as ``esp32_state`` events.
- WS   /api/v1/ws        : host clients (Gentle Dot daemon / TUI) receive actions,
                           ``esp32_state`` and turn events.
- GET  /api/v1/health    : liveness probe.

STT runs behind ``app.ports.Transcriber``, chosen by ``JARVIS_STT`` (``fake`` by
default, ``whisper`` for a local whisper.cpp server; see ``app.stt``); its result
and time go out as ``turn_stage{stage:"stt"}``. The agent (Gentle Shell) is still a
placeholder: ``/converse`` returns a canned reply, as a generated beep by default or
spoken by the configured synthesizer (see ``app.tts``), until the agent is chosen and
measured (docs/nube.md). The contracts are in docs/contratos.md. PC actions travel to the host over the WebSocket; the ESP32 never
receives them.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import struct
import time
import uuid
import wave
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.ports import Transcriber
from app.stt import make_transcriber
from app.stt.speed import SttSpeedWatch
from app.stt.whisper_server import WhisperServerError
from app.tts import make_synthesizer

logger = logging.getLogger("uvicorn.error")

DEVICE_TOKEN = os.environ.get("JARVIS_DEVICE_TOKEN", "dev-jarvis-token")
HOST_TOKEN = os.environ.get("JARVIS_HOST_TOKEN", "dev-host-token")
HEARTBEAT_TIMEOUT_S = 15.0
WATCHER_INTERVAL_S = 5.0

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"
REPLY_WAV = STATIC_DIR / "jarvis-reply.wav"
DEFAULT_REPLY_TEXT = "En orden, señor."

VALID_STATES = {"idle", "recording", "thinking", "speaking", "error"}


class Heartbeat(BaseModel):
    device_id: str = "esp32-s3"
    state: str = "idle"
    rssi: int | None = None
    ip: str | None = None


class Action(BaseModel):
    type: str
    value: str


class ConverseResponse(BaseModel):
    turn_id: str
    reply_text: str
    reply_audio_url: str
    actions: list[Action]


# In-memory device registry: device_id -> {"state", "rssi", "ip", "last_seen", "online"}
devices: dict[str, dict] = {}


class HostRegistry:
    """WebSocket connections from host clients (daemon / TUI)."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        async with self._lock:
            targets = list(self.clients)
        payload = _json_bytes(message)
        for ws in targets:
            try:
                await ws.send_bytes(payload)
            except (WebSocketDisconnect, RuntimeError, OSError):
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.clients.discard(ws)


def _json_bytes(message: dict) -> bytes:
    import json

    return json.dumps(message, separators=(",", ":")).encode("utf-8")


host_clients = HostRegistry()

# The STT stage (C3). Fails at import on an unknown JARVIS_STT value.
transcriber: Transcriber = make_transcriber()
# Warns (log once, turn_stage every time) when STT is slow enough to be on CPU (S5).
stt_watch = SttSpeedWatch.from_env()

# Created at import time so StaticFiles and the module import never fail on a
# fresh checkout.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="JARVIS orchestrator v0")


def require_device_token(x_device_token: str = Header()) -> None:
    if x_device_token != DEVICE_TOKEN:
        raise HTTPException(status_code=401, detail="invalid device token")


def generate_reply_wav(path: Path) -> None:
    """Generate a short 1 kHz beep as the placeholder reply audio."""
    sample_rate = 16000
    duration = 0.4
    total = int(sample_rate * duration)
    frames = bytearray()
    for i in range(total):
        envelope = min(1.0, i / 800.0, (total - i) / 800.0)
        sample = int(12000 * envelope * math.sin(2 * math.pi * 1000 * i / sample_rate))
        frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))


@app.on_event("startup")
async def startup() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if model := os.environ.get("JARVIS_PIPER_MODEL"):
        # Until the agent supplies dynamic text, render the fixed reply once.
        reply_wav = STATIC_DIR / "jarvis-spoken-reply.wav"
        synthesizer = make_synthesizer(Path(model))
        reply_wav.write_bytes(await synthesizer.synthesize(DEFAULT_REPLY_TEXT))
    else:
        reply_wav = REPLY_WAV
        if not reply_wav.exists():
            generate_reply_wav(reply_wav)
    app.state.reply_audio_url = f"/static/{reply_wav.name}"
    asyncio.create_task(watch_offline())


async def watch_offline() -> None:
    """Mark devices offline after HEARTBEAT_TIMEOUT_S without a heartbeat."""
    while True:
        await asyncio.sleep(WATCHER_INTERVAL_S)
        now = time.time()
        for device_id, dev in list(devices.items()):
            if dev["online"] and now - dev["last_seen"] > HEARTBEAT_TIMEOUT_S:
                dev["online"] = False
                await host_clients.broadcast(
                    {
                        "type": "esp32_state",
                        "device_id": device_id,
                        "state": dev["state"],
                        "rssi": dev["rssi"],
                        "ip": dev["ip"],
                        "online": False,
                        "ts": now,
                    }
                )


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "ok",
        "devices": {
            device_id: {"state": dev["state"], "online": dev["online"]}
            for device_id, dev in devices.items()
        },
    }


@app.post("/api/v1/heartbeat", dependencies=[Depends(require_device_token)])
async def heartbeat(hb: Heartbeat) -> dict:
    if hb.state not in VALID_STATES:
        raise HTTPException(
            status_code=422, detail=f"state must be one of {sorted(VALID_STATES)}"
        )
    now = time.time()
    devices[hb.device_id] = {
        "state": hb.state,
        "rssi": hb.rssi,
        "ip": hb.ip,
        "last_seen": now,
        "online": True,
    }
    await host_clients.broadcast(
        {
            "type": "esp32_state",
            "device_id": hb.device_id,
            "state": hb.state,
            "rssi": hb.rssi,
            "ip": hb.ip,
            "online": True,
            "ts": now,
        }
    )
    return {"status": "ok"}


@app.post("/api/v1/converse", dependencies=[Depends(require_device_token)])
async def converse(audio: UploadFile) -> ConverseResponse:
    # Save the clip and transcribe it; agent routing is not wired here yet, so the
    # reply is the fixed one (beep or synthesized at startup).
    started = time.perf_counter()
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty audio upload")
    turn_id = uuid.uuid4().hex
    await host_clients.broadcast(
        {
            "type": "turn_started",
            "turn_id": turn_id,
            "device_id": "esp32-s3",
            "ts": time.time(),
        }
    )
    clip_path = UPLOAD_DIR / f"{time.time():.0f}-{uuid.uuid4().hex[:8]}.wav"
    clip_path.write_bytes(data)

    stt_started = time.perf_counter()
    try:
        heard = await transcriber.transcribe(data)
    except Exception as error:
        # Any transcriber failure must close the turn that turn_started opened:
        # turn_error (C2) is the closing event; C1 defines no failure status yet.
        if isinstance(error, WhisperServerError):
            message = str(error)
            logger.warning("turn %s: stt failed: %s", turn_id, error)
        else:
            # Unexpected errors keep their details in the log, not on the wire.
            message = f"stt failed: {type(error).__name__}"
            logger.exception("turn %s: unexpected stt failure", turn_id)
        await host_clients.broadcast(
            {
                "type": "turn_error",
                "turn_id": turn_id,
                "stage": "stt",
                "message": message,
                "ts": time.time(),
            }
        )
        raise HTTPException(status_code=502, detail=message) from error
    stt_ms = round((time.perf_counter() - stt_started) * 1000, 1)
    logger.info("turn %s: stt %.1f ms: %r", turn_id, stt_ms, heard)
    stt_stage: dict = {
        "type": "turn_stage",
        "turn_id": turn_id,
        "stage": "stt",
        "ms": stt_ms,
        "text": heard,
        "ts": time.time(),
    }
    stt_warning = stt_watch.observe(stt_ms)
    if stt_warning is not None:
        stt_stage["warning"] = stt_warning
    await host_clients.broadcast(stt_stage)

    actions = [Action(type="media_key", value="play_pause")]
    await host_clients.broadcast(
        {
            "type": "action",
            "device_id": "esp32-s3",
            "action": actions[0].model_dump(),
            "ts": time.time(),
        }
    )
    reply_text = DEFAULT_REPLY_TEXT  # a later stage supplies the agent's reply
    await host_clients.broadcast(
        {
            "type": "turn_done",
            "turn_id": turn_id,
            "lane": "chat",
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
            "reply_text": reply_text,
            "ts": time.time(),
        }
    )
    return ConverseResponse(
        turn_id=turn_id,
        reply_text=reply_text,
        reply_audio_url=getattr(app.state, "reply_audio_url", f"/static/{REPLY_WAV.name}"),
        actions=actions,
    )


@app.websocket("/api/v1/ws")
async def host_ws(ws: WebSocket, token: str = "") -> None:
    if token != HOST_TOKEN:
        await ws.close(code=4401)
        return
    await host_clients.connect(ws)
    try:
        await ws.send_bytes(
            _json_bytes(
                {
                    "type": "esp32_snapshot",
                    "devices": [
                        {
                            "device_id": device_id,
                            "state": dev["state"],
                            "rssi": dev["rssi"],
                            "ip": dev["ip"],
                            "online": dev["online"],
                            "ts": dev["last_seen"],
                        }
                        for device_id, dev in devices.items()
                    ],
                }
            )
        )
        while True:
            # keepalive; host clients are read-only in v0
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        await host_clients.disconnect(ws)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
