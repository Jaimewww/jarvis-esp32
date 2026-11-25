# pyright: reportMissingImports=false
"""The WebSocket side of the bridge: hello, the token check, and frame fan-out (S2, S3).

The protocol is the one ``host/jarvis/client.py`` and ``backend/app/agents/gentle_dot.py``
speak: the first client frame is ``{"type": "hello", "token", "protocol": 1}``; a good
one gets ``ready`` and from then on every server frame carries a per-connection
monotonic ``seq``. A missing or wrong token closes with 4401, a malformed first frame or
another protocol version with 4400. A connection that carries an ``Origin`` header (a
browser page; Jarvis's clients send none) is refused with HTTP 403 before any of that
(S12.6). Turn frames go to every admitted client; the answer to
a request (history, the conversation list, the account, an ``error``) goes to its sender
only. A request the bridge does not support gets an ``error`` frame (S9).
"""

from __future__ import annotations

import asyncio
import ipaddress
import contextlib
import json
import logging
from http import HTTPStatus
from typing import Any, Final, Protocol

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .history import Conversation
from .hub import Hub
from .models import ModelError
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

    async def send(self, text: str) -> bool:
        """``False`` when the queue is full and the message was not taken."""
        ...

    async def abort(self) -> None: ...

    async def answer(self, request_id: str, response: dict[str, Any]) -> None: ...

    async def models(self) -> dict[str, Any]: ...

    async def set_model(self, provider: str, model_id: str, thinking: str | None) -> dict[str, Any]: ...

    async def clients_changed(self, count: int) -> None: ...


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


def _is_loopback(host: str) -> bool:
    """Whether ``host`` only accepts connections from this machine."""

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _refuse_browsers(connection: ServerConnection, request: Request) -> Response | None:
    if "Origin" in request.headers:
        logger.info("refused a client that sent an Origin header (a browser page)")
        return connection.respond(HTTPStatus.FORBIDDEN, "Browser pages may not connect.\n")
    return None


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
        self._conversation = Conversation()
        hub.observe(self._conversation.observe)
        self._server: Server | None = None

    async def start(self, host: str, port: int) -> int:
        """Listen on ``host:port`` and return the bound port."""

        self._server = await serve(
            self._handle,
            host,
            port,
            max_size=MAX_FRAME_BYTES,
            logger=_WIRE_LOGGER,
            process_request=_refuse_browsers,
        )
        bound = next(iter(self._server.sockets)).getsockname()[1]
        logger.info("listening on ws://%s:%d/ws", host, bound)
        if not _is_loopback(host):
            logger.warning(
                "listening on %s, outside loopback: other machines can reach the agent "
                "with the token; bind 127.0.0.1 unless you mean it",
                host,
            )
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
            "conversationId": self._conversation.id,
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
        await self._agent.clients_changed(self._hub.size)
        try:
            async for raw_frame in ws:
                await self._dispatch(ws, raw_frame)
        except ConnectionClosed:
            pass
        finally:
            self._hub.remove(ws)
            logger.info("client left")
            await self._agent.clients_changed(self._hub.size)

    async def _dispatch(self, ws: ServerConnection, raw: str | bytes) -> None:
        payload = _decode(raw)
        if payload is None:
            await self._error(ws, "bad_request", "The bridge reads JSON objects only.")
            return
        kind = payload.get("type")
        if kind == "send":
            text = payload.get("text")
            if isinstance(text, str) and text.strip() and not await self._agent.send(text):
                await self._error(ws, "queue_full", "Too many messages are waiting; try again when this reply ends.")
        elif kind == "abort":
            await self._agent.abort()
        elif kind == "ui_response":
            request_id = payload.get("requestId")
            if isinstance(request_id, str) and request_id:
                await self._agent.answer(request_id, payload)
            else:
                await self._error(ws, "bad_request", "A ui_response needs its requestId.")
        elif kind == "models_list":
            try:
                await self._hub.send_to(ws, await self._agent.models())
            except ModelError as error:
                await self._error(ws, error.code, error.message)
        elif kind == "model_set":
            await self._model_set(ws, payload)
        elif kind == "get_history":
            await self._hub.send_to(ws, self._conversation.history_frame())
        elif kind == "list_conversations":
            await self._hub.send_to(ws, self._conversation.conversations_frame())
        elif kind == "auth_list":
            await self._hub.send_to(ws, self._accounts())
        elif kind in ("auth_login", "auth_reply", "auth_logout"):
            await self._error(ws, "auth_unsupported", "Pi keeps its own accounts: sign in with pi itself.")
        else:
            name = kind[:40] if isinstance(kind, str) else "that request"
            logger.debug("refused an unsupported client frame")
            await self._error(ws, "unsupported", f"The Pi bridge does not support {name}.")

    async def _model_set(self, ws: ServerConnection, payload: dict[str, Any]) -> None:
        provider, model_id, thinking = payload.get("provider"), payload.get("id"), payload.get("thinking")
        if not isinstance(provider, str) or not isinstance(model_id, str) or not (
            thinking is None or isinstance(thinking, str)
        ):
            await self._error(ws, "model_unavailable", "A model_set needs a provider and a model id.")
            return
        try:
            frame = await self._agent.set_model(provider, model_id, thinking)
        except ModelError as error:
            await self._error(ws, error.code, error.message)
            return
        await self._hub.broadcast(frame)

    def _accounts(self) -> dict[str, Any]:
        """Pi signs in on its own: the running model's provider is the one, ready, account."""

        provider = self._agent.model.partition("/")[0] or "pi"
        account = {"id": provider, "name": provider, "methods": [], "configured": True, "source": "pi"}
        return {"type": "auth_providers", "providers": [account], "open": False}

    async def _error(self, ws: ServerConnection, code: str, message: str) -> None:
        await self._hub.send_to(ws, {"type": "error", "code": code, "message": message})
