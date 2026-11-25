# Pyright: "websockets" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""Read-only client for the Jarvis backend channel (contract C2, docs/contratos.md).

The backend serves ``ws://<pc>:8000/api/v1/ws?token=<t>`` and pushes JSON events in
binary frames: the state of each ESP32 and the progress of every voice turn. Nothing
is sent back. Event types and fields this module does not know are ignored, so the
backend can add events without breaking the window.

A rejected token ends the client. The contract names close code 4401; the backend
refuses before accepting the socket, which a client sees as HTTP 403 on the
handshake. Both mean the same here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote

from websockets.exceptions import ConnectionClosed

from .client import (
    BACKOFF_SECONDS,
    CLOSE_UNAUTHORIZED,
    HELLO_DEADLINE_SECONDS,
    MAX_FRAME_BYTES,
    ConnectionStatus,
    _close_code,
    _decode,
    _describe,
    _optional_text,
    _records,
    _text,
    _ws_connect,
)

logger = logging.getLogger(__name__)

DEFAULT_BACKEND_URL: Final = "ws://127.0.0.1:8000/api/v1/ws"
TOKEN_ENV: Final = "JARVIS_HOST_TOKEN"
DEFAULT_BACKEND_TOKEN: Final = "dev-host-token"
HTTP_FORBIDDEN: Final = 403

_REJECTED_TOKEN: Final = (
    f"The backend refused the token. Set {TOKEN_ENV} to the value the backend uses."
)


def default_token() -> str:
    """The token from the environment, or the development default of the backend."""

    return os.environ.get(TOKEN_ENV) or DEFAULT_BACKEND_TOKEN


# --- Events the TUI renders ---------------------------------------------------


@dataclass(slots=True, frozen=True)
class Device:
    """The last state one ESP32 reported."""

    device_id: str
    state: str
    rssi: int | None = None
    ip: str | None = None
    online: bool = False
    ts: float | None = None


@dataclass(slots=True, frozen=True)
class SnapshotEvent:
    """Every known device, sent once on connect."""

    devices: tuple[Device, ...]


@dataclass(slots=True, frozen=True)
class DeviceEvent:
    device: Device


@dataclass(slots=True, frozen=True)
class ActionEvent:
    """One catalogue action of a turn (contract C4)."""

    device_id: str
    action_type: str
    value: Any = None
    ts: float | None = None


@dataclass(slots=True, frozen=True)
class TurnStarted:
    turn_id: str
    device_id: str
    ts: float | None = None


@dataclass(slots=True, frozen=True)
class TurnStage:
    turn_id: str
    stage: str
    ms: float | None = None
    text: str | None = None
    ts: float | None = None
    # Set on a slow ``stt`` stage: the backend thinks whisper-server runs on CPU.
    warning: str | None = None


@dataclass(slots=True, frozen=True)
class TurnDone:
    turn_id: str
    lane: str
    total_ms: float | None = None
    reply_text: str = ""
    ts: float | None = None


@dataclass(slots=True, frozen=True)
class TurnError:
    turn_id: str
    stage: str
    message: str
    ts: float | None = None


BackendEvent = (
    SnapshotEvent
    | DeviceEvent
    | ActionEvent
    | TurnStarted
    | TurnStage
    | TurnDone
    | TurnError
)

BackendEventCallback = Callable[[BackendEvent], None]
BackendStatusCallback = Callable[[ConnectionStatus, str], None]


# --- Parsing ------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _parse_device(raw: dict[str, Any]) -> Device:
    rssi = raw.get("rssi")
    return Device(
        device_id=_text(raw.get("device_id"), "unknown"),
        state=_text(raw.get("state"), "unknown"),
        rssi=rssi if isinstance(rssi, int) and not isinstance(rssi, bool) else None,
        ip=_optional_text(raw.get("ip")),
        online=raw.get("online") is True,
        ts=_number(raw.get("ts")),
    )


def parse_backend_event(payload: dict[str, Any]) -> BackendEvent | None:
    """Turn one backend payload into an event, or ``None`` for types we do not know."""

    kind = payload.get("type")
    ts = _number(payload.get("ts"))
    if kind == "esp32_snapshot":
        return SnapshotEvent(tuple(_parse_device(raw) for raw in _records(payload.get("devices"))))
    if kind == "esp32_state":
        return DeviceEvent(_parse_device(payload))
    if kind == "action":
        raw = payload.get("action")
        if not isinstance(raw, dict):
            return None
        return ActionEvent(
            device_id=_text(payload.get("device_id"), "unknown"),
            action_type=_text(raw.get("type"), "unknown"),
            value=raw.get("value"),
            ts=ts,
        )
    turn_id = _text(payload.get("turn_id"))
    if kind == "turn_started":
        return TurnStarted(turn_id, _text(payload.get("device_id"), "unknown"), ts)
    if kind == "turn_stage":
        return TurnStage(
            turn_id,
            _text(payload.get("stage"), "unknown"),
            _number(payload.get("ms")),
            _optional_text(payload.get("text")),
            ts,
            _optional_text(payload.get("warning")),
        )
    if kind == "turn_done":
        return TurnDone(
            turn_id,
            _text(payload.get("lane"), "unknown"),
            _number(payload.get("total_ms")),
            _text(payload.get("reply_text")),
            ts,
        )
    if kind == "turn_error":
        return TurnError(
            turn_id,
            _text(payload.get("stage"), "unknown"),
            _text(payload.get("message")),
            ts,
        )
    return None


def _handshake_status(error: BaseException) -> int | None:
    """The HTTP status of a refused handshake, across websockets releases."""

    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


# --- Client -------------------------------------------------------------------


class BackendClient:
    """One backend connection at a time, reconnected with backoff. It only listens."""

    def __init__(
        self,
        on_event: BackendEventCallback,
        on_status: BackendStatusCallback | None = None,
        *,
        url: str = DEFAULT_BACKEND_URL,
        token: str | None = None,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
    ) -> None:
        #: The address without the token, safe to show and to log.
        self.url = url
        self._token = default_token() if token is None else token
        self._backoff = backoff
        self._on_event = on_event
        self._on_status = on_status
        self._status = ConnectionStatus.DISCONNECTED
        self._status_detail = "not connected"
        self._socket: Any | None = None
        self._attempts = 0
        self._stopped = True
        self._closed = asyncio.Event()

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def status_detail(self) -> str:
        return self._status_detail

    async def run(self) -> None:
        """Stay connected until ``close`` is called or the token is refused."""

        self._stopped = False
        self._closed.clear()
        while not self._stopped:
            unauthorized, reason = await self._session()
            if self._stopped:
                break
            if unauthorized:
                self._set_status(ConnectionStatus.UNAUTHORIZED, reason)
                return
            delay = self._backoff[min(self._attempts, len(self._backoff) - 1)]
            self._attempts += 1
            self._set_status(ConnectionStatus.DISCONNECTED, f"{reason} Retrying in {delay:g}s.")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._closed.wait(), delay)
        self._set_status(ConnectionStatus.DISCONNECTED, "not connected")

    async def close(self) -> None:
        """Stop connecting and drop the socket."""

        self._stopped = True
        self._closed.set()
        socket, self._socket = self._socket, None
        if socket is not None:
            with contextlib.suppress(Exception):
                await socket.close()

    async def _session(self) -> tuple[bool, str]:
        """One connection; returns whether the token was refused, and why it ended."""

        self._set_status(ConnectionStatus.CONNECTING, f"Connecting to {self.url}")
        separator = "&" if "?" in self.url else "?"
        try:
            socket = await asyncio.wait_for(
                _ws_connect(
                    f"{self.url}{separator}token={quote(self._token, safe='')}",
                    open_timeout=HELLO_DEADLINE_SECONDS,
                    close_timeout=2.0,
                    max_size=MAX_FRAME_BYTES,
                ),
                HELLO_DEADLINE_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - any failure here is "cannot connect"
            if _handshake_status(error) == HTTP_FORBIDDEN:
                return True, _REJECTED_TOKEN
            # The exception text can carry the full address, token included: log the type.
            logger.debug("connect to %s failed: %s", self.url, type(error).__name__)
            if _handshake_status(error) is not None:
                detail = f"HTTP {_handshake_status(error)}"
            else:
                detail = _describe(error)
            return False, f"Cannot reach the backend at {self.url}: {detail}."

        self._socket = socket
        self._attempts = 0
        self._set_status(ConnectionStatus.CONNECTED, f"Connected to {self.url}")
        try:
            while True:
                payload = _decode(await socket.recv())
                if payload is not None:
                    self._dispatch(payload)
        except ConnectionClosed as error:
            code = _close_code(error)
            if code == CLOSE_UNAUTHORIZED:
                return True, _REJECTED_TOKEN
            suffix = "" if code is None else f" (close code {code})"
            return False, f"The backend closed the connection{suffix}."
        finally:
            self._socket = None
            with contextlib.suppress(Exception):
                await socket.close()

    def _dispatch(self, payload: dict[str, Any]) -> None:
        event = parse_backend_event(payload)
        if event is None:
            return
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a rendering bug must not drop the connection
            logger.exception("event handler failed for %s", type(event).__name__)

    def _set_status(self, status: ConnectionStatus, detail: str) -> None:
        changed = status is not self._status or detail != self._status_detail
        self._status = status
        self._status_detail = detail
        if not changed or self._on_status is None:
            return
        try:
            self._on_status(status, detail)
        except Exception:  # noqa: BLE001 - the TUI must not break the connection
            logger.exception("status handler failed")
