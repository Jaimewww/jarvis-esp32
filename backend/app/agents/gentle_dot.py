"""Gentle Dot adapter: ``app.ports.Agent`` (contract C3) over the daemon's protocol v1.

The long-task lane of D14. The daemon is upstream Gentle Dot, unmodified (D1); this
module is the only place that knows its protocol, as ``host/jarvis/client.py``
documents it:

- ``ws://127.0.0.1:4317/ws``; the first client frame is
  ``{"type": "hello", "token": <token>, "protocol": 1}`` and the daemon answers
  ``ready``. Close code 4401 means the token was refused, 4400 an unsupported
  protocol version.
- ``{"type": "send", "text": ...}`` asks the agent; the daemon echoes it as
  ``user_message`` and streams the answer as ``message_delta`` frames followed by
  one ``message_done`` (``text``, ``stopped``, ``error``). ``{"type": "abort"}``
  stops the answer that is running. ``error`` frames carry ``code`` and ``message``.

One connection per ``reply``: the daemon has a single agent and a single active
conversation for all its clients (D14), so the adapter runs one reply at a time.
The answer is re-chunked by sentence so the caller can synthesize the first one
while the daemon keeps working (D17).

C3 defines no error model, so failures are raised as ``GentleDotError`` subclasses.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

if TYPE_CHECKING:
    from app.ports import Agent

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_URL: Final = "ws://127.0.0.1:4317/ws"
DEFAULT_TOKEN_PATH: Final = Path("~/.gentle-dot/token")
DEFAULT_OPEN_TIMEOUT_S: Final = 5.0
DEFAULT_IDLE_TIMEOUT_S: Final = 120.0
ABORT_SEND_TIMEOUT_S: Final = 1.0
MAX_FRAME_BYTES: Final = 2 * 1024 * 1024

CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_BAD_PROTOCOL: Final = 4400

ENV_URL: Final = "JARVIS_GENTLE_DOT_URL"
ENV_TOKEN: Final = "JARVIS_GENTLE_DOT_TOKEN"
ENV_TOKEN_FILE: Final = "JARVIS_GENTLE_DOT_TOKEN_FILE"
ENV_OPEN_TIMEOUT: Final = "JARVIS_GENTLE_DOT_OPEN_TIMEOUT_S"
ENV_IDLE_TIMEOUT: Final = "JARVIS_GENTLE_DOT_IDLE_TIMEOUT_S"

# A sentence ends at . ! ? or … (plus closing quotes/brackets) followed by
# whitespace; "3.5" or a terminator at the end of the buffer does not end one.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”»)\]]*\s+")


# --- Errors ------------------------------------------------------------------------


class GentleDotError(RuntimeError):
    """Any failure of the Gentle Dot adapter."""


class GentleDotUnavailable(GentleDotError):
    """The daemon cannot be reached or dropped the connection."""


class GentleDotUnauthorized(GentleDotError):
    """The daemon refused the access token (close 4401 or a rejected handshake)."""


class GentleDotProtocolMismatch(GentleDotError):
    """The daemon does not speak protocol v1 (close 4400)."""


class GentleDotTimeout(GentleDotError):
    """The daemon did not answer in time."""


class GentleDotStopped(GentleDotError):
    """The answer was stopped by someone other than this adapter."""


class GentleDotBusy(GentleDotError):
    """A reply is already running; the daemon does one task at a time (D14)."""


class GentleDotTokenError(GentleDotError):
    """The access token is not configured or cannot be read."""


class GentleDotUnsupported(GentleDotError):
    """The daemon protocol v1 has no way to do what was asked."""


class GentleDotDaemonError(GentleDotError):
    """The daemon reported an error (``error`` frame or ``message_done.error``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"Gentle Dot error {code}: {message}")
        self.code = code
        self.message = message


# --- Sentences ---------------------------------------------------------------------


class _Sentences:
    """Buffers streamed text and releases it one sentence at a time."""

    def __init__(self) -> None:
        self._buffer = ""
        self.received = ""  # everything fed so far

    def feed(self, text: str) -> list[str]:
        self.received += text
        self._buffer += text
        out: list[str] = []
        while match := _SENTENCE_END.search(self._buffer):
            sentence = self._buffer[: match.end()].strip()
            self._buffer = self._buffer[match.end() :]
            if sentence:
                out.append(sentence)
        return out

    def finish(self, final_text: str) -> list[str]:
        """Flush, letting ``message_done.text`` fill in deltas that went missing."""

        out: list[str] = []
        if final_text and final_text != self.received:
            if final_text.startswith(self.received):
                out += self.feed(final_text[len(self.received) :])
            elif not self.received:
                out += self.feed(final_text)
            else:
                logger.debug("message_done text differs from the deltas; keeping the deltas")
        tail = self._buffer.strip()
        self._buffer = ""
        return out + ([tail] if tail else [])


# --- Adapter -----------------------------------------------------------------------


def _decode(raw: str | bytes) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _close_code(error: ConnectionClosed) -> int | None:
    close = error.rcvd or error.sent
    return close.code if close is not None else None


class GentleDotAgent:
    """``Agent`` (C3) backed by the Gentle Dot daemon, for the long-task lane."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        token: str | Callable[[], str] | None = None,
        token_path: Path | str = DEFAULT_TOKEN_PATH,
        open_timeout: float = DEFAULT_OPEN_TIMEOUT_S,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
    ) -> None:
        self.url = url
        self.token_path = Path(token_path)
        self.open_timeout = open_timeout
        self.idle_timeout = idle_timeout
        self._token = token
        self._socket: ClientConnection | None = None
        self._busy = False
        self._abort_requested = False
        self._message_done = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GentleDotAgent:
        """Configure from ``JARVIS_GENTLE_DOT_*`` variables; the token is read per reply."""

        env = os.environ if environ is None else environ
        token = env.get(ENV_TOKEN) or None
        return cls(
            env.get(ENV_URL) or DEFAULT_URL,
            token=token,
            token_path=env.get(ENV_TOKEN_FILE) or DEFAULT_TOKEN_PATH,
            open_timeout=float(env.get(ENV_OPEN_TIMEOUT) or DEFAULT_OPEN_TIMEOUT_S),
            idle_timeout=float(env.get(ENV_IDLE_TIMEOUT) or DEFAULT_IDLE_TIMEOUT_S),
        )

    @property
    def busy(self) -> bool:
        return self._busy

    def read_token(self) -> str:
        """The configured token, or the content of the token file."""

        if callable(self._token):
            return self._token()
        if self._token:
            return self._token
        path = self.token_path.expanduser()
        try:
            token = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise GentleDotTokenError(
                f"No Gentle Dot access token at {path}; start the daemon once or set {ENV_TOKEN}."
            ) from error
        except OSError as error:
            raise GentleDotTokenError(
                f"Cannot read the Gentle Dot access token at {path}: {error.strerror or error}"
            ) from error
        if not token:
            raise GentleDotTokenError(f"The Gentle Dot access token at {path} is empty.")
        return token

    # C3

    async def reply(self, text: str, *, turn_id: str) -> AsyncIterator[str]:
        """Send ``text`` to the daemon and yield its answer one sentence at a time."""

        if self._busy:
            raise GentleDotBusy("A Gentle Dot task is already running.")
        self._busy = True
        self._abort_requested = False
        self._message_done = False
        socket: ClientConnection | None = None
        try:
            token = self.read_token()
            socket = await self._open()
            await self._hello(socket, token)
            self._socket = socket
            logger.debug("turn %s: sending to Gentle Dot", turn_id)
            await socket.send(json.dumps({"type": "send", "text": text}))
            async for chunk in self._answer(socket, text):
                yield chunk
        finally:
            self._socket = None
            if socket is not None:
                if not self._message_done and not self._abort_requested:
                    # Timeout, error frame, lost link or a cancelled caller: do not leave
                    # the daemon working on a task nobody is waiting for.
                    await self._send_abort(socket)
                with contextlib.suppress(Exception):
                    await socket.close()
            self._busy = False

    async def abort(self) -> None:
        """Ask the daemon to stop the running answer; nothing to do if none runs."""

        socket = self._socket
        if socket is None:
            return
        self._abort_requested = True
        await self._send_abort(socket)

    async def reset(self) -> None:
        """Not available: protocol v1 has no client message to start a new conversation."""

        raise GentleDotUnsupported(
            "Gentle Dot protocol v1 has no message to forget the conversation."
        )

    # Protocol

    async def _open(self) -> ClientConnection:
        try:
            return await connect(
                self.url,
                open_timeout=self.open_timeout,
                close_timeout=2.0,
                max_size=MAX_FRAME_BYTES,
            )
        except InvalidStatus as error:
            status = error.response.status_code
            if status in (401, 403):
                raise GentleDotUnauthorized(
                    f"Gentle Dot at {self.url} rejected the connection (HTTP {status})."
                ) from error
            raise GentleDotUnavailable(
                f"Gentle Dot at {self.url} rejected the connection (HTTP {status})."
            ) from error
        except TimeoutError as error:
            raise GentleDotTimeout(
                f"Gentle Dot at {self.url} did not open within {self.open_timeout:g}s."
            ) from error
        except (OSError, InvalidHandshake) as error:
            raise GentleDotUnavailable(f"Cannot reach Gentle Dot at {self.url}: {error}") from error

    async def _hello(self, socket: ClientConnection, token: str) -> None:
        hello = {"type": "hello", "token": token, "protocol": PROTOCOL_VERSION}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.open_timeout
        try:
            await socket.send(json.dumps(hello))
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                payload = _decode(await asyncio.wait_for(socket.recv(), remaining))
                if payload is not None and payload.get("type") == "ready":
                    return
        except TimeoutError as error:
            raise GentleDotTimeout(
                f"Gentle Dot sent no ready within {self.open_timeout:g}s."
            ) from error
        except ConnectionClosed as error:
            raise self._closed_error(error) from error

    async def _answer(self, socket: ClientConnection, text: str) -> AsyncIterator[str]:
        """Yield the sentences of the assistant message that answers ``text``."""

        sentences = _Sentences()
        user_id: str | None = None
        reply_id: str | None = None
        wanted = text.strip()
        while True:
            try:
                raw = await asyncio.wait_for(socket.recv(), self.idle_timeout)
            except TimeoutError as error:
                raise GentleDotTimeout(
                    f"Gentle Dot sent nothing for {self.idle_timeout:g}s."
                ) from error
            except ConnectionClosed as error:
                raise self._closed_error(error) from error
            payload = _decode(raw)
            if payload is None:
                continue
            kind = payload.get("type")
            if kind == "error":
                raise GentleDotDaemonError(
                    _text(payload.get("code")) or "error", _text(payload.get("message"))
                )
            if user_id is None:
                # Frames before our echo belong to an earlier message.
                if kind == "user_message" and _text(payload.get("text")).strip() == wanted:
                    user_id = _text(payload.get("messageId"))
                continue
            if kind not in ("message_delta", "message_done"):
                continue
            message_id = _text(payload.get("messageId"))
            if message_id == user_id:
                continue
            if reply_id is None:
                reply_id = message_id
            elif message_id != reply_id:
                continue
            if kind == "message_delta":
                for sentence in sentences.feed(_text(payload.get("delta"))):
                    yield sentence
                continue
            # message_done: the daemon is no longer working on it, whatever the outcome.
            self._message_done = True
            if payload.get("stopped") is True:
                if self._abort_requested:
                    return  # our abort: drop the cut-off tail
                raise GentleDotStopped("The Gentle Dot answer was stopped by another client.")
            error_text = _text(payload.get("error"))
            if error_text:
                raise GentleDotDaemonError("message_failed", error_text)
            for sentence in sentences.finish(_text(payload.get("text"))):
                yield sentence
            return

    async def _send_abort(self, socket: ClientConnection) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                socket.send(json.dumps({"type": "abort"})), ABORT_SEND_TIMEOUT_S
            )

    def _closed_error(self, error: ConnectionClosed) -> GentleDotError:
        code = _close_code(error)
        if code == CLOSE_UNAUTHORIZED:
            return GentleDotUnauthorized(
                "Gentle Dot refused the access token (close code 4401)."
            )
        if code == CLOSE_BAD_PROTOCOL:
            return GentleDotProtocolMismatch(
                f"Gentle Dot does not speak protocol {PROTOCOL_VERSION} (close code 4400)."
            )
        suffix = f" (close code {code})" if code is not None else ""
        return GentleDotUnavailable(f"Gentle Dot closed the connection{suffix}.")


if TYPE_CHECKING:

    def _conforms(agent: GentleDotAgent) -> Agent:  # checked by pyright, never run
        return agent
