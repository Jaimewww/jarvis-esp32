# Pyright: "websockets" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""WebSocket client for the Gentle Dot daemon (protocol version 1).

The daemon listens on ``ws://127.0.0.1:4317/ws``. The first frame a client sends is
``{"type": "hello", "token": <access token>, "protocol": 1}``; the daemon answers
``ready`` and then every frame from the server carries a per-connection monotonic
``seq``. Close code 4401 means the token was refused, 4400 means the protocol
version is not supported.

This module holds no widgets: it turns frames into typed events, keeps the
connection alive with the same backoff as the desktop app (500, 1000, 2000, 5000
ms) and hands events to a caller-supplied callback, so ``jarvis.tui`` only renders.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from websockets.exceptions import ConnectionClosed

try:  # websockets >= 13 keeps the asyncio client here.
    from websockets.asyncio.client import connect as _ws_connect
except ImportError:  # pragma: no cover - depends on the installed websockets release
    from websockets import connect as _ws_connect  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

PROTOCOL_VERSION: Final = 1
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 4317
DEFAULT_PATH: Final = "/ws"
DEFAULT_TOKEN_PATH: Final = Path("~/.gentle-dot/token")

CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_BAD_PROTOCOL: Final = 4400

#: Reasoning effort levels the daemon accepts in ``model_set`` (protocol ``THINKING_LEVELS``).
THINKING_LEVELS: Final = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# The daemon gives a client a moment to say hello; the same window applies here.
HELLO_DEADLINE_SECONDS: Final = 5.0
BACKOFF_SECONDS: Final = (0.5, 1.0, 2.0, 5.0)
MAX_FRAME_BYTES: Final = 2 * 1024 * 1024

#: The websockets library logs every frame at debug level, and an ``auth_reply`` frame
#: can hold an API key: its logger never goes below info, whatever ``--log-level`` is.
_WIRE_LOGGER: Final = logging.getLogger(f"{__name__}.wire")
_WIRE_LOGGER.setLevel(logging.INFO)


class ConnectionStatus(StrEnum):
    """What the status indicator shows."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    UNAUTHORIZED = "unauthorized"
    PROTOCOL_MISMATCH = "protocol_mismatch"


class TokenError(RuntimeError):
    """The access token could not be read from disk."""


# --- Events the TUI renders ---------------------------------------------------


@dataclass(slots=True)
class Ready:
    """The daemon accepted the connection."""

    agent_state: str = "starting"
    conversation_id: str | None = None
    model: str | None = None
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentStateEvent:
    state: str


@dataclass(slots=True)
class UserMessageEvent:
    message_id: str
    text: str


@dataclass(slots=True)
class MessageDeltaEvent:
    message_id: str
    delta: str


@dataclass(slots=True)
class MessageDoneEvent:
    message_id: str
    text: str
    stopped: bool = False
    error: str | None = None


@dataclass(slots=True)
class Activity:
    """One thing the agent did on the computer, as the action log shows it."""

    id: str
    kind: str
    title: str
    status: str


@dataclass(slots=True)
class ActivityEvent:
    message_id: str
    activity: Activity


@dataclass(slots=True)
class Ask:
    """A question the desktop app asks; this client only shows that it happened."""

    request_id: str
    method: str
    title: str
    message: str | None = None


@dataclass(slots=True)
class AskEvent:
    ask: Ask


@dataclass(slots=True)
class AskResolvedEvent:
    request_id: str


@dataclass(slots=True)
class ToastEvent:
    level: str
    message: str


@dataclass(slots=True)
class HistoryMessage:
    id: str
    role: str
    text: str
    activities: tuple[Activity, ...] = ()
    earlier: bool = False


@dataclass(slots=True)
class HistoryEvent:
    messages: tuple[HistoryMessage, ...]
    conversation_id: str | None = None
    has_earlier: bool = False


@dataclass(slots=True)
class EarlierEvent:
    before: str
    messages: tuple[HistoryMessage, ...]
    has_earlier: bool = False


@dataclass(slots=True)
class Conversation:
    id: str
    title: str
    updated_at: str


@dataclass(slots=True)
class ConversationsEvent:
    conversations: tuple[Conversation, ...]
    active_id: str | None = None


@dataclass(slots=True)
class QueueEvent:
    """Messages waiting for the assistant while it is busy."""

    steering: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()


@dataclass(slots=True)
class ErrorEvent:
    """An ``error`` frame, code and message as the daemon words them (e.g. ``thinking_unavailable``)."""

    code: str
    message: str


# Sign-in (``/login``). The answers a person types never come back from the daemon, so
# none of these events can carry a secret.

#: Sign-in methods of protocol v1.
AUTH_METHODS: Final = ("oauth", "api_key")
#: Kinds of ``auth_prompt``: ``secret`` is typed hidden, ``select`` picks an option.
AUTH_PROMPT_KINDS: Final = ("text", "secret", "select", "manual_code")
#: Kinds of ``auth_event``.
AUTH_EVENT_KINDS: Final = ("info", "auth_url", "device_code", "progress")
#: ``error`` codes the daemon answers an ``auth_login``, ``auth_reply`` or ``auth_logout``
#: with; each one ends the sign-in this window is running.
AUTH_ERRORS: Final = frozenset({"auth_busy", "auth_unsupported", "unknown_provider", "auth_flow_not_found"})


@dataclass(slots=True)
class AuthProvider:
    """An AI account Gentle Dot can sign in to."""

    id: str
    name: str
    methods: tuple[str, ...]
    configured: bool = False
    #: Name of the subscription sign-in, for example "Claude Pro/Max".
    oauth_name: str | None = None
    #: Where the working credential comes from, for example "stored" or "environment".
    source: str | None = None


@dataclass(slots=True)
class AuthProvidersEvent:
    providers: tuple[AuthProvider, ...]
    #: The daemon asks the window to show its sign-in screen.
    open: bool = False


@dataclass(slots=True)
class AuthOption:
    id: str
    label: str
    description: str | None = None


@dataclass(slots=True)
class AuthPrompt:
    flow_id: str
    kind: str
    message: str
    placeholder: str | None = None
    options: tuple[AuthOption, ...] = ()
    #: The answer may be left empty.
    optional: bool = False


@dataclass(slots=True)
class AuthPromptEvent:
    prompt: AuthPrompt


@dataclass(slots=True)
class AuthLink:
    url: str
    label: str | None = None


@dataclass(slots=True)
class AuthFlowEvent:
    """One step of a sign-in (``auth_event``); the fields of its ``kind`` are set."""

    flow_id: str
    kind: str
    message: str = ""
    links: tuple[AuthLink, ...] = ()
    url: str = ""
    instructions: str | None = None
    user_code: str = ""
    verification_uri: str = ""
    expires_in_seconds: int | None = None


@dataclass(slots=True)
class AuthDoneEvent:
    flow_id: str
    provider_id: str
    ok: bool
    message: str | None = None


@dataclass(slots=True)
class ModelChoice:
    """A model of a connected account; ``thinking_levels`` is empty when it does not think."""

    provider: str
    id: str
    name: str
    images: bool = False
    thinking_levels: tuple[str, ...] = ()

    @property
    def spec(self) -> str:
        return f"{self.provider}/{self.id}"


@dataclass(slots=True)
class ModelGroup:
    provider: str
    name: str
    models: tuple[ModelChoice, ...] = ()


@dataclass(slots=True)
class ModelRef:
    """The main assistant's model; ``thinking`` only when the model thinks."""

    provider: str
    id: str
    name: str
    thinking: str | None = None

    @property
    def spec(self) -> str:
        return f"{self.provider}/{self.id}"


@dataclass(slots=True)
class ModelsEvent:
    """The ``models`` reply to ``models_list`` (also broadcast after ``model_set``)."""

    current: ModelRef | None = None
    #: A switch chosen during a reply, waiting for the next message.
    next: ModelRef | None = None
    groups: tuple[ModelGroup, ...] = ()

    def choice(self, provider: str, model_id: str) -> ModelChoice | None:
        for group in self.groups:
            for model in group.models:
                if model.provider == provider and model.id == model_id:
                    return model
        return None


Event = (
    Ready
    | AgentStateEvent
    | UserMessageEvent
    | MessageDeltaEvent
    | MessageDoneEvent
    | ActivityEvent
    | AskEvent
    | AskResolvedEvent
    | ToastEvent
    | HistoryEvent
    | EarlierEvent
    | ConversationsEvent
    | QueueEvent
    | ErrorEvent
    | AuthProvidersEvent
    | AuthPromptEvent
    | AuthFlowEvent
    | AuthDoneEvent
    | ModelsEvent
)

EventCallback = Callable[[Event], None]
StatusCallback = Callable[[ConnectionStatus, str], None]


# --- Token --------------------------------------------------------------------


def read_token(path: Path | None = None) -> str:
    """Read the daemon's access token, with a message a person can act on."""

    token_path = Path(DEFAULT_TOKEN_PATH if path is None else path).expanduser()
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise TokenError(
            f"No access token at {token_path}. Start Gentle Dot once so it creates one."
        ) from error
    except OSError as error:
        reason = error.strerror or str(error)
        raise TokenError(f"Cannot read the access token at {token_path}: {reason}") from error
    if not token:
        raise TokenError(f"The access token at {token_path} is empty.")
    return token


# --- Parsing ------------------------------------------------------------------


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def parse_model_spec(spec: str) -> tuple[str, str]:
    """Split ``provider/model-id``; ``ValueError`` when either side is missing."""

    provider, slash, model_id = spec.strip().partition("/")
    if not slash or not provider or not model_id:
        raise ValueError(f"expected provider/model-id, got {spec!r}")
    return provider, model_id


def _parse_model_ref(raw: Any) -> ModelRef | None:
    if not isinstance(raw, dict):
        return None
    provider, model_id = _text(raw.get("provider")), _text(raw.get("id"))
    if not provider or not model_id:
        return None
    thinking = raw.get("thinking")
    return ModelRef(
        provider=provider,
        id=model_id,
        name=_text(raw.get("name"), model_id),
        thinking=thinking if thinking in THINKING_LEVELS else None,
    )


def _parse_models(payload: dict[str, Any]) -> ModelsEvent:
    groups: list[ModelGroup] = []
    for raw_group in _records(payload.get("groups")):
        models = tuple(
            ModelChoice(
                provider=_text(raw.get("provider")),
                id=_text(raw.get("id")),
                name=_text(raw.get("name"), _text(raw.get("id"))),
                images=raw.get("images") is True,
                thinking_levels=tuple(
                    level for level in _strings(raw.get("thinkingLevels")) if level in THINKING_LEVELS
                ),
            )
            for raw in _records(raw_group.get("models"))
        )
        provider = _text(raw_group.get("provider"))
        groups.append(ModelGroup(provider, _text(raw_group.get("name"), provider), models))
    return ModelsEvent(
        current=_parse_model_ref(payload.get("current")),
        next=_parse_model_ref(payload.get("next")),
        groups=tuple(groups),
    )


def _parse_activity(raw: dict[str, Any]) -> Activity:
    return Activity(
        id=_text(raw.get("id")),
        kind=_text(raw.get("kind"), "other"),
        title=_text(raw.get("title")),
        status=_text(raw.get("status"), "running"),
    )


def _parse_history_messages(value: Any) -> tuple[HistoryMessage, ...]:
    messages: list[HistoryMessage] = []
    for raw in _records(value):
        role = _text(raw.get("role"), "assistant")
        messages.append(
            HistoryMessage(
                id=_text(raw.get("id")),
                role=role if role in ("user", "assistant") else "assistant",
                text=_text(raw.get("text")),
                activities=tuple(_parse_activity(item) for item in _records(raw.get("activities"))),
                earlier=raw.get("earlier") is True,
            )
        )
    return tuple(messages)


def _parse_providers(value: Any) -> tuple[AuthProvider, ...]:
    providers: list[AuthProvider] = []
    for raw in _records(value):
        provider_id = _text(raw.get("id"))
        if not provider_id:
            continue
        providers.append(
            AuthProvider(
                id=provider_id,
                name=_text(raw.get("name")) or provider_id,
                methods=tuple(m for m in _strings(raw.get("methods")) if m in AUTH_METHODS),
                configured=raw.get("configured") is True,
                oauth_name=_optional_text(raw.get("oauthName")),
                source=_optional_text(raw.get("source")),
            )
        )
    return tuple(providers)


def _parse_prompt(raw: Any) -> AuthPrompt | None:
    if not isinstance(raw, dict) or raw.get("kind") not in AUTH_PROMPT_KINDS:
        return None
    options = tuple(
        AuthOption(
            id=_text(option.get("id")),
            label=_text(option.get("label")) or _text(option.get("id")),
            description=_optional_text(option.get("description")),
        )
        for option in _records(raw.get("options"))
        if _text(option.get("id"))
    )
    return AuthPrompt(
        flow_id=_text(raw.get("flowId")),
        kind=raw["kind"],
        message=_text(raw.get("message")),
        placeholder=_optional_text(raw.get("placeholder")),
        options=options,
        optional=raw.get("optional") is True,
    )


def _parse_flow_event(flow_id: str, raw: Any) -> AuthFlowEvent | None:
    if not isinstance(raw, dict) or raw.get("kind") not in AUTH_EVENT_KINDS:
        return None
    expires = raw.get("expiresInSeconds")
    return AuthFlowEvent(
        flow_id=flow_id,
        kind=raw["kind"],
        message=_text(raw.get("message")),
        links=tuple(
            AuthLink(_text(link.get("url")), _optional_text(link.get("label")))
            for link in _records(raw.get("links"))
            if _text(link.get("url"))
        ),
        url=_text(raw.get("url")),
        instructions=_optional_text(raw.get("instructions")),
        user_code=_text(raw.get("userCode")),
        verification_uri=_text(raw.get("verificationUri")),
        expires_in_seconds=expires if isinstance(expires, int) and not isinstance(expires, bool) else None,
    )


def _parse_auth(kind: str, payload: dict[str, Any]) -> Event | None:
    if kind == "auth_providers":
        return AuthProvidersEvent(_parse_providers(payload.get("providers")), open=payload.get("open") is True)
    if kind == "auth_prompt":
        prompt = _parse_prompt(payload.get("prompt"))
        return None if prompt is None else AuthPromptEvent(prompt)
    if kind == "auth_event":
        return _parse_flow_event(_text(payload.get("flowId")), payload.get("event"))
    if kind == "auth_done":
        return AuthDoneEvent(
            flow_id=_text(payload.get("flowId")),
            provider_id=_text(payload.get("providerId")),
            ok=payload.get("ok") is True,
            message=_optional_text(payload.get("message")),
        )
    return None


def parse_event(payload: dict[str, Any]) -> Event | None:
    """Turn one server payload into an event, or ``None`` for payloads we do not show."""

    kind = payload.get("type")
    if kind == "ready":
        features = payload.get("features")
        return Ready(
            agent_state=_text(payload.get("agentState"), "starting"),
            conversation_id=_optional_text(payload.get("conversationId")),
            model=_optional_text(payload.get("model")),
            features=dict(features) if isinstance(features, dict) else {},
        )
    if kind == "agent_state":
        return AgentStateEvent(_text(payload.get("state"), "idle"))
    if kind == "user_message":
        return UserMessageEvent(_text(payload.get("messageId")), _text(payload.get("text")))
    if kind == "message_delta":
        return MessageDeltaEvent(_text(payload.get("messageId")), _text(payload.get("delta")))
    if kind == "message_done":
        return MessageDoneEvent(
            message_id=_text(payload.get("messageId")),
            text=_text(payload.get("text")),
            stopped=payload.get("stopped") is True,
            error=_optional_text(payload.get("error")),
        )
    if kind == "activity":
        raw = payload.get("activity")
        if not isinstance(raw, dict):
            return None
        return ActivityEvent(_text(payload.get("messageId")), _parse_activity(raw))
    if kind == "ask":
        raw = payload.get("ask")
        if not isinstance(raw, dict):
            return None
        return AskEvent(
            Ask(
                request_id=_text(raw.get("requestId")),
                method=_text(raw.get("method"), "confirm"),
                title=_text(raw.get("title")),
                message=_optional_text(raw.get("message")),
            )
        )
    if kind == "ask_resolved":
        return AskResolvedEvent(_text(payload.get("requestId")))
    if kind == "toast":
        return ToastEvent(_text(payload.get("level"), "info"), _text(payload.get("message")))
    if kind == "history":
        return HistoryEvent(
            messages=_parse_history_messages(payload.get("messages")),
            conversation_id=_optional_text(payload.get("conversationId")),
            has_earlier=payload.get("hasEarlier") is True,
        )
    if kind == "earlier":
        return EarlierEvent(
            before=_text(payload.get("before")),
            messages=_parse_history_messages(payload.get("messages")),
            has_earlier=payload.get("hasEarlier") is True,
        )
    if kind == "conversations":
        conversations = tuple(
            Conversation(
                id=_text(raw.get("id")),
                title=_text(raw.get("title")),
                updated_at=_text(raw.get("updatedAt")),
            )
            for raw in _records(payload.get("conversations"))
        )
        return ConversationsEvent(conversations, active_id=_optional_text(payload.get("activeId")))
    if kind == "queue":
        return QueueEvent(
            steering=_strings(payload.get("steering")),
            follow_up=_strings(payload.get("followUp")),
        )
    if kind == "models":
        return _parse_models(payload)
    if kind == "error":
        return ErrorEvent(_text(payload.get("code"), "error"), _text(payload.get("message")))
    if isinstance(kind, str) and kind.startswith("auth_"):
        return _parse_auth(kind, payload)
    return None


def _decode(raw: str | bytes) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        logger.debug("ignoring a frame that is not JSON")
        return None
    return payload if isinstance(payload, dict) else None


def _close_code(error: BaseException) -> int | None:
    """The close code of a ConnectionClosed, across websockets releases."""

    for attribute in ("rcvd", "sent"):
        close = getattr(error, attribute, None)
        code = getattr(close, "code", None)
        if isinstance(code, int):
            return code
    # websockets < 14 keeps the code on the exception itself.
    code = getattr(error, "code", None)
    return code if isinstance(code, int) else None


def _describe(error: BaseException) -> str:
    if isinstance(error, ConnectionRefusedError):
        return "connection refused (is the daemon running?)"
    if isinstance(error, TimeoutError):
        return "timed out"
    reason = getattr(error, "strerror", None)
    if isinstance(reason, str) and reason:
        return reason
    message = str(error).strip()
    return message or type(error).__name__


# --- Client -------------------------------------------------------------------


class JarvisClient:
    """One daemon connection at a time, reconnected with backoff."""

    def __init__(
        self,
        on_event: EventCallback,
        on_status: StatusCallback | None = None,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        path: str = DEFAULT_PATH,
        url: str | None = None,
        token_path: Path | str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.url = url or f"ws://{host}:{port}{path}"
        self.token_path = Path(DEFAULT_TOKEN_PATH if token_path is None else token_path)
        self._on_event = on_event
        self._on_status = on_status
        self._status = ConnectionStatus.DISCONNECTED
        self._status_detail = "not connected"
        self._socket: Any | None = None
        self._outbox: asyncio.Queue[str] | None = None
        self._last_seq: int | None = None
        self._awaiting_history = False
        self._connect_error = ""
        self._attempts = 0
        self._stopped = True
        self._closed = asyncio.Event()

    # Public state

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def status_detail(self) -> str:
        return self._status_detail

    @property
    def connected(self) -> bool:
        return self._status is ConnectionStatus.CONNECTED

    # Public actions; they report whether the frame could be queued.

    def send(self, text: str) -> bool:
        """Ask the assistant; the daemon echoes it back as ``user_message``."""

        return self._queue({"type": "send", "text": text})

    def abort(self) -> bool:
        """Stop the answer that is running."""

        return self._queue({"type": "abort"})

    def steer(self, text: str) -> bool:
        """Add a message the assistant reads after its current step."""

        return self._queue({"type": "steer", "text": text})

    # Sign-in. ``auth_reply`` may carry a secret: frames are never logged (see
    # ``_WIRE_LOGGER``) and nothing here keeps the value once it is queued.

    def auth_list(self) -> bool:
        """Ask for the accounts; the daemon answers ``auth_providers``."""

        return self._queue({"type": "auth_list"})

    def auth_login(self, provider_id: str, method: str) -> bool:
        """Start a sign-in (``oauth`` or ``api_key``)."""

        return self._queue({"type": "auth_login", "providerId": provider_id, "method": method})

    def auth_reply(self, flow_id: str, value: str | None = None, *, cancelled: bool = False) -> bool:
        """Answer the prompt of a sign-in, or cancel the sign-in."""

        if cancelled:
            return self._queue({"type": "auth_reply", "flowId": flow_id, "cancelled": True})
        return self._queue({"type": "auth_reply", "flowId": flow_id, "value": value or ""})

    def auth_logout(self, provider_id: str) -> bool:
        """Sign out of one account."""

        return self._queue({"type": "auth_logout", "providerId": provider_id})

    def models_list(self) -> bool:
        """Ask for the current model and the connected accounts' models (``models`` reply)."""

        return self._queue({"type": "models_list"})

    def model_set(self, provider: str, model_id: str, thinking: str | None = None) -> bool:
        """Switch the main assistant's model; the daemon saves it as its default.

        It applies from the next message when a reply is running. The daemon answers
        ``models`` on success or an ``error`` (``model_unavailable``,
        ``thinking_unavailable``, ``model_switch_failed``). ``ValueError`` for a
        thinking level the protocol does not know.
        """

        if thinking is not None and thinking not in THINKING_LEVELS:
            raise ValueError(f"unknown thinking level {thinking!r}; one of {', '.join(THINKING_LEVELS)}")
        message: dict[str, Any] = {"type": "model_set", "provider": provider, "id": model_id}
        if thinking is not None:
            message["thinking"] = thinking
        return self._queue(message)

    # Lifetime

    async def run(self) -> None:
        """Stay connected until ``close`` is called."""

        self._stopped = False
        self._closed.clear()
        while not self._stopped:
            code, reason = await self._session()
            if self._stopped:
                break
            if code == CLOSE_UNAUTHORIZED:
                self._set_status(ConnectionStatus.UNAUTHORIZED, reason)
                break
            status = (
                ConnectionStatus.PROTOCOL_MISMATCH
                if code == CLOSE_BAD_PROTOCOL
                else ConnectionStatus.DISCONNECTED
            )
            delay = BACKOFF_SECONDS[min(self._attempts, len(BACKOFF_SECONDS) - 1)]
            self._attempts += 1
            self._set_status(status, f"{reason} Retrying in {delay:g}s.")
            await self._wait(delay)
        if self._stopped:
            self._set_status(ConnectionStatus.DISCONNECTED, "not connected")

    async def close(self) -> None:
        """Stop connecting and drop the socket."""

        self._stopped = True
        self._closed.set()
        socket, self._socket = self._socket, None
        if socket is not None:
            with contextlib.suppress(Exception):
                await socket.close()

    # One connection

    async def _session(self) -> tuple[int | None, str]:
        self._last_seq = None
        self._awaiting_history = False
        self._outbox = asyncio.Queue()
        self._set_status(ConnectionStatus.CONNECTING, f"Connecting to {self.url}")

        try:
            token = read_token(self.token_path)
        except TokenError as error:
            self._outbox = None
            return None, str(error)

        socket = await self._open_socket()
        if socket is None:
            self._outbox = None
            return None, self._connect_error

        self._socket = socket
        sender = asyncio.create_task(self._send_loop(socket), name="jarvis-sender")
        code: int | None = None
        reason = "The daemon closed the connection."
        try:
            await self._hello(socket, token)
            self._attempts = 0
            self._set_status(ConnectionStatus.CONNECTED, f"Connected to {self.url}")
            for request in ("get_history", "list_conversations", "auth_list"):
                await self._outbox.put(json.dumps({"type": request}))
            await self._read_loop(socket)
        except ConnectionClosed as error:
            code = _close_code(error)
            if code == CLOSE_UNAUTHORIZED:
                reason = (
                    "The access token was refused (close code 4401). "
                    f"Check {self.token_path.expanduser()} and that the daemon is yours."
                )
            elif code == CLOSE_BAD_PROTOCOL:
                reason = (
                    "The daemon does not speak protocol 1 (close code 4400). "
                    "Update this client or the Gentle Dot build."
                )
            elif code is None:
                reason = "The daemon closed the connection."
            else:
                reason = f"The daemon closed the connection (close code {code})."
        except TimeoutError:
            reason = f"No answer to the hello within {HELLO_DEADLINE_SECONDS:g}s."
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            self._socket = None
            self._outbox = None
            with contextlib.suppress(Exception):
                await socket.close()
            if self._status is ConnectionStatus.CONNECTED:
                self._set_status(ConnectionStatus.DISCONNECTED, reason)
        return code, reason

    async def _open_socket(self) -> Any | None:
        """Connect, or return ``None`` after recording why in ``_connect_error``."""

        try:
            return await asyncio.wait_for(
                _ws_connect(
                    self.url,
                    open_timeout=HELLO_DEADLINE_SECONDS,
                    close_timeout=2.0,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=MAX_FRAME_BYTES,
                    logger=_WIRE_LOGGER,
                ),
                HELLO_DEADLINE_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - any failure here is "cannot connect"
            logger.debug("connect to %s failed", self.url, exc_info=error)
            self._connect_error = f"Cannot reach the daemon at {self.url}: {_describe(error)}."
            return None

    async def _hello(self, socket: Any, token: str) -> None:
        hello = json.dumps(
            {"type": "hello", "token": token, "protocol": PROTOCOL_VERSION}
        )
        await asyncio.wait_for(socket.send(hello), HELLO_DEADLINE_SECONDS)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HELLO_DEADLINE_SECONDS
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("no ready frame")
            payload = _decode(await asyncio.wait_for(socket.recv(), remaining))
            if payload is None:
                continue
            self._dispatch(payload)
            if payload.get("type") == "ready":
                return

    async def _read_loop(self, socket: Any) -> None:
        while True:
            payload = _decode(await socket.recv())
            if payload is not None:
                self._dispatch(payload)

    async def _send_loop(self, socket: Any) -> None:
        outbox = self._outbox
        if outbox is None:
            raise RuntimeError("the sender started without an outbox")
        while True:
            frame = await outbox.get()
            await socket.send(frame)

    # Frames

    def _dispatch(self, payload: dict[str, Any]) -> None:
        seq = payload.get("seq")
        if isinstance(seq, int):
            previous = self._last_seq
            self._last_seq = seq
            if previous is not None and seq > previous + 1 and not self._awaiting_history:
                # A frame went missing: the only safe repair is a fresh history page.
                logger.debug("seq gap %s -> %s, asking for history", previous, seq)
                self._awaiting_history = True
                self._queue_frame({"type": "get_history"})
        event = parse_event(payload)
        if event is None:
            return
        if isinstance(event, HistoryEvent):
            self._awaiting_history = False
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a rendering bug must not drop the connection
            logger.exception("event handler failed for %s", type(event).__name__)

    def _queue(self, message: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        return self._queue_frame(message)

    def _queue_frame(self, message: dict[str, Any]) -> bool:
        outbox = self._outbox
        if outbox is None:
            return False
        outbox.put_nowait(json.dumps(message))
        return True

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

    async def _wait(self, delay: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._closed.wait(), delay)
