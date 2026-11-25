# pyright: reportMissingImports=false
"""The WebSocket side of the bridge: hello, the token check, and frame fan-out (S2, S3).

The protocol is the one ``host/jarvis/client.py`` and ``backend/app/agents/gentle_dot.py``
speak: the first client frame is ``{"type": "hello", "token", "protocol": 1}``; a good
one gets ``ready`` and from then on every server frame carries a per-connection
monotonic ``seq``. A missing or wrong token closes with 4401, a malformed first frame or
another protocol version with 4400. Turn frames go to every admitted client.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any, Final, Protocol

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .token import token_matches

logger = logging.getLogger(__name__)

PROTOCOL_VERSION: Final = 1
CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_BAD_PROTOCOL: Final = 4400
HELLO_TIMEOUT_S: Final = 5.0
MAX_FRAME_BYTES: Final = 2 * 1024 * 1024

#: websockets logs every frame at debug level, and the hello frame holds the token:
#: its logger never goes below info, whatever the log level is.
_WIRE_LOGGER: Final = logging.getLogger(f"{__name__}.wire")
_WIRE_LOGGER.setLevel(logging.INFO)


class TurnAgent(Protocol):
    """What the server needs from the turn side (``jarvis.pibridge.turns.Bridge``)."""

    @property
    def agent_state(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def send(self, text: str) -> None: ...

    async def abort(self) -> None: ...


def _decode(raw: str | bytes) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def check_hello(raw: str | bytes, token: str) -> int | None:
    """``None`` for a valid hello, else the close code to answer it with."""

    payload = _decode(raw)
    if payload is None or payload.get("type") != "hello":
        return CLOSE_BAD_PROTOCOL
    protocol = payload.get("protocol")
    if isinstance(protocol, bool) or protocol != PROTOCOL_VERSION:
        return CLOSE_BAD_PROTOCOL
    if not token_matches(token, payload.get("token")):
        return CLOSE_UNAUTHORIZED
    return None


class Hub:
    """The admitted connections, each with its own ``seq``; frames go out in one order."""

    def __init__(self) -> None:
        self._seq: dict[ServerConnection, int] = {}
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._seq)

    async def admit(self, ws: ServerConnection, ready: dict[str, Any]) -> None:
        async with self._lock:
            self._seq[ws] = 0
            await self._send(ws, ready)

    def remove(self, ws: ServerConnection) -> None:
        self._seq.pop(ws, None)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            for ws in list(self._seq):
                await self._send(ws, payload)

    async def _send(self, ws: ServerConnection, payload: dict[str, Any]) -> None:
        seq = self._seq.get(ws)
        if seq is None:
            return
        self._seq[ws] = seq + 1
        try:
            await ws.send(json.dumps({**payload, "seq": seq + 1}))
        except ConnectionClosed:
            self.remove(ws)


class BridgeServer:
    def __init__(
        self,
        *,
        token: str,
        agent: TurnAgent,
        hub: Hub,
        hello_timeout: float = HELLO_TIMEOUT_S,
    ) -> None:
        self._token = token
        self._agent = agent
        self._hub = hub
        self._hello_timeout = hello_timeout
        self._conversation_id = uuid.uuid4().hex
        self._server: Server | None = None

    async def start(self, host: str, port: int) -> int:
        """Listen on ``host:port`` and return the bound port."""

        self._server = await serve(
            self._handle, host, port, max_size=MAX_FRAME_BYTES, logger=_WIRE_LOGGER
        )
        bound = next(iter(self._server.sockets)).getsockname()[1]
        logger.info("listening on ws://%s:%d/ws", host, bound)
        return int(bound)

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None

    def _ready(self) -> dict[str, Any]:
        return {
            "type": "ready",
            "agentState": self._agent.agent_state,
            "conversationId": self._conversation_id,
            "model": self._agent.model,
            "features": {"conversations": False},
        }

    async def _handle(self, ws: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), self._hello_timeout)
        except TimeoutError:
            logger.info("refused a client: no hello within %gs", self._hello_timeout)
            await ws.close(CLOSE_UNAUTHORIZED, "no hello")
            return
        except ConnectionClosed:
            return
        refused = check_hello(raw, self._token)
        if refused is not None:
            logger.info("refused a client (close %d)", refused)
            reason = "unauthorized" if refused == CLOSE_UNAUTHORIZED else "unsupported protocol"
            await ws.close(refused, reason)
            return
        logger.info("client admitted")
        await self._hub.admit(ws, self._ready())
        try:
            async for raw_frame in ws:
                await self._dispatch(raw_frame)
        except ConnectionClosed:
            pass
        finally:
            self._hub.remove(ws)
            logger.info("client left")

    async def _dispatch(self, raw: str | bytes) -> None:
        payload = _decode(raw)
        if payload is None:
            return
        kind = payload.get("type")
        if kind == "send":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                await self._agent.send(text)
        elif kind == "abort":
            await self._agent.abort()
        else:
            logger.debug("ignoring an unsupported client frame")
