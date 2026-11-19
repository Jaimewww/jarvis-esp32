#!/usr/bin/env python3
"""Device simulator (contract C1, docs/contratos.md). Standard library only.

Plays one voice turn the way the ESP32 will: heartbeats through
idle -> recording -> thinking -> speaking -> idle, the WAV posted to
/api/v1/converse between thinking and speaking, and the reply audio downloaded
while "speaking".

    python tools/sim_device.py --url http://127.0.0.1:8000 --token <t> [--wav file.wav]

Without --wav it sends a generated 16 kHz mono 16-bit tone. Exits 1 on any HTTP
or connection error.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

SAMPLE_RATE = 16000


def tone_wav(seconds: float = 1.0, freq: float = 440.0) -> bytes:
    total = int(SAMPLE_RATE * seconds)
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)))
        for i in range(total)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(frames)
    return buf.getvalue()


def check_wav(data: bytes, name: str) -> None:
    """Warn (do not fail) when a WAV is not PCM 16 kHz mono 16-bit (D6)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            fmt = (wf.getframerate(), wf.getnchannels(), wf.getsampwidth())
    except (wave.Error, EOFError) as exc:
        print(f"warning: {name} is not a readable WAV: {exc}", file=sys.stderr)
        return
    if fmt != (SAMPLE_RATE, 1, 2):
        print(
            f"warning: {name} is {fmt[0]} Hz, {fmt[1]} ch, {fmt[2] * 8}-bit; "
            "the contract is 16000 Hz, mono, 16-bit",
            file=sys.stderr,
        )


class Device:
    def __init__(self, url: str, token: str, device_id: str) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.device_id = device_id

    def _send(self, req: urllib.request.Request) -> bytes:
        req.add_header("X-Device-Token", self.token)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    def heartbeat(self, state: str) -> None:
        body = json.dumps(
            {"device_id": self.device_id, "state": state, "rssi": -50, "ip": "127.0.0.1"}
        ).encode()
        req = urllib.request.Request(
            f"{self.url}/api/v1/heartbeat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self._send(req)
        print(f"heartbeat  {state}")

    def converse(self, wav: bytes) -> dict:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="clip.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode() + wav + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.url}/api/v1/converse",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return json.loads(self._send(req))

    def download(self, path: str) -> bytes:
        return self._send(urllib.request.Request(f"{self.url}{path}"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate the ESP32 for one voice turn.")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--token",
        default=os.environ.get("JARVIS_DEVICE_TOKEN", "dev-jarvis-token"),
        help="device token (default: $JARVIS_DEVICE_TOKEN, as the backend)",
    )
    parser.add_argument("--wav", help="WAV to send (default: generated 1 s tone)")
    parser.add_argument("--device-id", default="esp32-s3")
    args = parser.parse_args()

    if args.wav:
        try:
            with open(args.wav, "rb") as fh:
                wav = fh.read()
        except OSError as exc:
            print(f"error: cannot read {args.wav}: {exc}", file=sys.stderr)
            return 1
        check_wav(wav, args.wav)
    else:
        wav = tone_wav()

    dev = Device(args.url, args.token, args.device_id)
    step = "heartbeat"
    try:
        dev.heartbeat("idle")
        dev.heartbeat("recording")
        dev.heartbeat("thinking")
        step = "converse"
        t0 = time.perf_counter()
        reply = dev.converse(wav)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"converse   {elapsed_ms:.1f} ms")
        for key in ("turn_id", "reply_text", "reply_audio_url", "actions"):
            print(f"  {key}: {json.dumps(reply.get(key), ensure_ascii=False)}")
        step = "heartbeat"
        dev.heartbeat("speaking")
        if reply.get("reply_audio_url"):
            step = "reply audio"
            audio = dev.download(reply["reply_audio_url"])
            print(f"reply audio {len(audio)} bytes")
            check_wav(audio, "reply audio")
        step = "heartbeat"
        dev.heartbeat("idle")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"error: {step} -> HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        print(f"error: {step} -> cannot reach {args.url}: {reason}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"error: {step} -> response is not JSON: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
