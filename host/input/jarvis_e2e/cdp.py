"""A tiny Chrome DevTools Protocol client over a stdlib WebSocket (no third-party packages).

Only what the desktop E2E needs: list page targets over HTTP, connect to one, call methods and
evaluate expressions. Events are read and dropped while waiting for a reply.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class CdpError(Exception):
    pass


def encode_frame(payload: bytes, opcode: int = OP_TEXT, mask: bytes | None = None) -> bytes:
    """One final client frame (clients must mask, RFC 6455 5.3)."""
    mask = mask if mask is not None else os.urandom(4)
    if len(mask) != 4:
        raise ValueError("mask must be 4 bytes")
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + masked


def parse_header(data: bytes) -> tuple[bool, int, int, bytes | None, int] | None:
    """(fin, opcode, payload length, mask or None, header length), or None if `data` is short."""
    if len(data) < 2:
        return None
    fin, opcode = bool(data[0] & 0x80), data[0] & 0x0F
    masked, n = bool(data[1] & 0x80), data[1] & 0x7F
    pos = 2
    if n == 126:
        if len(data) < 4:
            return None
        n = struct.unpack("!H", data[2:4])[0]
        pos = 4
    elif n == 127:
        if len(data) < 10:
            return None
        n = struct.unpack("!Q", data[2:10])[0]
        pos = 10
    mask = None
    if masked:
        if len(data) < pos + 4:
            return None
        mask = data[pos:pos + 4]
        pos += 4
    return fin, opcode, n, mask, pos


def devtools_port(user_data_dir: Path) -> int | None:
    """The port Chromium/Electron wrote to <user-data-dir>/DevToolsActivePort (first line)."""
    try:
        first = (user_data_dir / "DevToolsActivePort").read_text().splitlines()[0]
    except (OSError, IndexError):
        return None
    return int(first) if first.isdigit() else None


def list_targets(port: int) -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
        data = json.loads(r.read().decode())
    return data if isinstance(data, list) else []


class Cdp:
    """A connection to one target's webSocketDebuggerUrl (ws://127.0.0.1:<port>/…)."""

    def __init__(self, ws_url: str, timeout: float = 10.0) -> None:
        parts = urlsplit(ws_url)
        if parts.scheme != "ws" or parts.hostname not in ("127.0.0.1", "localhost"):
            raise CdpError(f"unexpected websocket url: {ws_url}")
        self.timeout = timeout
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("websocket handshake: connection closed")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CdpError(f"websocket handshake refused: {head[:80]!r}")
        self.next_id = 0

    def close(self) -> None:
        try:
            self.sock.sendall(encode_frame(b"", OP_CLOSE))
        except OSError:
            pass
        self.sock.close()

    def _read_frame(self) -> tuple[int, bytes, bool]:
        while (h := parse_header(self.buf)) is None or len(self.buf) < h[4] + h[2]:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("websocket closed")
            self.buf += chunk
        fin, opcode, n, mask, pos = h
        payload, self.buf = self.buf[pos:pos + n], self.buf[pos + n:]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload, fin

    def _read_message(self) -> bytes:
        parts: list[bytes] = []
        while True:
            opcode, payload, fin = self._read_frame()
            if opcode == OP_PING:
                self.sock.sendall(encode_frame(payload, OP_PONG))
                continue
            if opcode == OP_CLOSE:
                raise CdpError("websocket closed by peer")
            if opcode in (OP_TEXT, OP_BIN, OP_CONT):
                parts.append(payload)
                if fin:
                    return b"".join(parts)

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        mid = self.next_id
        self.sock.sendall(encode_frame(json.dumps({"id": mid, "method": method,
                                                   "params": params or {}}).encode()))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            msg = json.loads(self._read_message())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CdpError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise CdpError(f"{method}: no reply")

    def evaluate(self, expression: str) -> Any:
        res = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                             "awaitPromise": True})
        if "exceptionDetails" in res:
            raise CdpError(f"evaluate: {res['exceptionDetails'].get('text')}: {expression[:80]}")
        return res.get("result", {}).get("value")
