# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""Textual front end for the Gentle Dot daemon and the Jarvis backend.

The window holds the conversation, the agent's state, and the log of what the agent
did on the computer, all from the Gentle Dot daemon. It sends what the user types and
can stop a run; it never answers an ``ask`` approval, because those belong to the
desktop app that owns the private channel to the daemon (S4).

A second, read-only connection to the Jarvis backend (contract C2) adds the state of
the ESP32 and the progress of each voice turn. The two connections are independent:
either one can be down without hiding the other.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from datetime import datetime
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Input, RichLog, Static

from .backend import (
    DEFAULT_BACKEND_TOKEN,
    DEFAULT_BACKEND_URL,
    TOKEN_ENV,
    ActionEvent,
    BackendClient,
    BackendEvent,
    Device,
    DeviceEvent,
    SnapshotEvent,
    TurnDone,
    TurnError,
    TurnStage,
    TurnStarted,
)
from .client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TOKEN_PATH,
    ActivityEvent,
    AgentStateEvent,
    AskEvent,
    AskResolvedEvent,
    ConnectionStatus,
    ConversationsEvent,
    EarlierEvent,
    ErrorEvent,
    Event,
    HistoryEvent,
    JarvisClient,
    MessageDeltaEvent,
    MessageDoneEvent,
    QueueEvent,
    Ready,
    ToastEvent,
    UserMessageEvent,
)

logger = logging.getLogger(__name__)

STATE_LABELS = {
    "starting": "STARTING",
    "idle": "IDLE",
    "thinking": "THINKING",
    "working": "WORKING",
    "needs_you": "NEEDS YOU",
    "restarting": "RESTARTING",
    "error": "ERROR",
}
STATE_STYLES = {
    "starting": "dim",
    "idle": "green",
    "thinking": "cyan",
    "working": "yellow",
    "needs_you": "magenta",
    "restarting": "blue",
    "error": "bold red",
}
LINK_LABELS = {
    ConnectionStatus.DISCONNECTED: ("DISCONNECTED", "red"),
    ConnectionStatus.CONNECTING: ("CONNECTING", "yellow"),
    ConnectionStatus.CONNECTED: ("CONNECTED", "green"),
    ConnectionStatus.UNAUTHORIZED: ("UNAUTHORIZED", "bold red"),
    ConnectionStatus.PROTOCOL_MISMATCH: ("BAD PROTOCOL", "bold red"),
}
ACTIVITY_STYLES = {"running": "yellow", "done": "green", "failed": "bold red"}
TOAST_STYLES = {"info": "cyan", "warning": "yellow", "error": "bold red"}
ROLE_LABELS = {"user": "You", "assistant": "Jarvis"}
TURN_ID_WIDTH = 8


def _milliseconds(value: float | None) -> str:
    return "? ms" if value is None else f"{value:.0f} ms"


class JarvisApp(App[None]):
    """The host window: chat, agent state, action log."""

    TITLE = "Jarvis — Gentle Dot"
    SUB_TITLE = "host TUI (protocol v1)"
    CSS = """
    Screen {
        layout: vertical;
    }
    #status {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    #device {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    #detail {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #body {
        height: 1fr;
    }
    #conversation {
        width: 3fr;
        padding: 0 1;
    }
    .message {
        padding: 0 1;
        margin-bottom: 1;
    }
    .message-user {
        color: $text;
        background: $boost;
    }
    .message-assistant {
        color: $text;
    }
    #side {
        width: 2fr;
    }
    #turns {
        height: 1fr;
        border-bottom: solid $boost;
    }
    #actions {
        height: 2fr;
    }
    #prompt {
        height: 3;
    }
    """
    BINDINGS = (
        Binding("ctrl+a", "abort_run", "Abort", priority=True),
        Binding("ctrl+q", "quit", "Quit"),
    )

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        url: str | None = None,
        token_path: Path | None = None,
        backend_url: str = DEFAULT_BACKEND_URL,
        backend_token: str | None = None,
    ) -> None:
        super().__init__()
        self._backend = BackendClient(
            on_event=self._on_backend_event,
            on_status=self._on_backend_status,
            url=backend_url,
            token=backend_token,
        )
        self._backend_task: asyncio.Task[None] | None = None
        self._backend_status = ConnectionStatus.DISCONNECTED
        self._devices: dict[str, Device] = {}
        self._client = JarvisClient(
            on_event=self._on_event,
            on_status=self._on_status,
            host=host,
            port=port,
            url=url,
            token_path=token_path,
        )
        self._client_task: asyncio.Task[None] | None = None
        self._agent_state = "starting"
        self._model: str | None = None
        self._conversation_title: str | None = None
        self._queue_total = 0
        self._last_detail = ""
        self._message_widgets: dict[str, Static] = {}
        self._message_bodies: dict[str, str] = {}

    # Layout

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        yield Static("", id="device")
        yield Static("", id="detail")
        with Horizontal(id="body"):
            yield VerticalScroll(id="conversation")
            with Vertical(id="side"):
                yield RichLog(id="turns", highlight=False, markup=True, wrap=True)
                yield RichLog(id="actions", highlight=False, markup=True, wrap=True)
        yield Input(
            placeholder="Message Jarvis — Enter sends, Ctrl+A stops the answer",
            id="prompt",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._render_status()
        self._render_device()
        self._log("info", f"connecting to {self._client.url}")
        self.query_one("#prompt", Input).focus()
        self._client_task = asyncio.create_task(self._client.run(), name="jarvis-client")
        self._client_task.add_done_callback(self._client_finished)
        self._backend_task = asyncio.create_task(self._backend.run(), name="jarvis-backend")
        self._backend_task.add_done_callback(self._backend_finished)

    async def on_unmount(self) -> None:
        await self._client.close()
        await self._backend.close()
        for task in (self._client_task, self._backend_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _backend_finished(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("the backend client stopped", exc_info=error)
            self._turn_log("error", f"the backend client stopped: {escape(str(error))}")

    def _client_finished(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("the daemon client stopped", exc_info=error)
            self._log("error", f"the daemon client stopped: {error}")

    # Connection status

    def _on_status(self, status: ConnectionStatus, detail: str) -> None:
        self._render_status()
        if status is ConnectionStatus.CONNECTED:
            self._set_detail(f"connected to {self._client.url}")
            self._log("info", f"connected to {self._client.url}")
            return
        self._set_detail(detail)
        level = "warning" if status is ConnectionStatus.CONNECTING else "error"
        if status is ConnectionStatus.DISCONNECTED:
            level = "warning"
        self._log(level, detail)

    def _set_detail(self, detail: str) -> None:
        if detail == self._last_detail:
            return
        self._last_detail = detail
        shortened = detail if len(detail) <= 100 else f"{detail[:97]}…"
        self.query_one("#detail", Static).update(escape(shortened))

    def _render_status(self) -> None:
        label = STATE_LABELS.get(self._agent_state, self._agent_state.upper() or "UNKNOWN")
        state_style = STATE_STYLES.get(self._agent_state, "white")
        link_label, link_style = LINK_LABELS.get(
            self._client.status, (self._client.status.value.upper(), "white")
        )
        parts = [
            f"[{state_style}]AGENT {label}[/]",
            f"[{link_style}]LINK {link_label}[/]",
        ]
        if self._model:
            parts.append(f"[dim]{escape(self._model)}[/]")
        if self._conversation_title:
            parts.append(f"[dim]{escape(self._conversation_title)}[/]")
        if self._queue_total:
            parts.append(f"[yellow]{self._queue_total} queued[/]")
        self.query_one("#status", Static).update(" │ ".join(parts))

    # Backend: ESP32 state and voice turns

    def _on_backend_status(self, status: ConnectionStatus, detail: str) -> None:
        previous, self._backend_status = self._backend_status, status
        self._render_device()
        # Every retry changes the detail; only a change of state is worth a line.
        if status is previous or status is ConnectionStatus.CONNECTING:
            return
        level = "info" if status is ConnectionStatus.CONNECTED else "warning"
        if status is ConnectionStatus.UNAUTHORIZED:
            level = "error"
        self._turn_log(level, escape(detail))

    def _render_device(self) -> None:
        parts: list[str] = []
        if not self._devices:
            parts.append("[dim]ESP32 no device yet[/]")
        for device in self._devices.values():
            name = escape(device.device_id)
            if not device.online:
                parts.append(f"[red]ESP32 {name} OFFLINE[/]")
                continue
            parts.append(f"[green]ESP32 {name} {escape(device.state.upper())}[/]")
            if device.rssi is not None:
                parts.append(f"[dim]{device.rssi} dBm[/]")
            if device.ip:
                parts.append(f"[dim]{escape(device.ip)}[/]")
        link_label, link_style = LINK_LABELS.get(
            self._backend_status, (self._backend_status.value.upper(), "white")
        )
        parts.append(f"[{link_style}]BACKEND {link_label}[/]")
        self.query_one("#device", Static).update(" │ ".join(parts))

    def _on_backend_event(self, event: BackendEvent) -> None:
        if isinstance(event, SnapshotEvent):
            self._devices = {device.device_id: device for device in event.devices}
            self._render_device()
            return
        if isinstance(event, DeviceEvent):
            self._devices[event.device.device_id] = event.device
            self._render_device()
            return
        if isinstance(event, ActionEvent):
            value = "" if event.value is None else f" {escape(str(event.value))}"
            self._turn_log("action", f"action {escape(event.action_type)}{value}")
            return
        turn = escape(event.turn_id[:TURN_ID_WIDTH] or "?")
        if isinstance(event, TurnStarted):
            self._turn_log("info", f"turn {turn} started ({escape(event.device_id)})")
        elif isinstance(event, TurnStage):
            text = f": {escape(event.text)}" if event.text else ""
            self._turn_log(
                "info", f"turn {turn} {escape(event.stage)} {_milliseconds(event.ms)}{text}"
            )
        elif isinstance(event, TurnDone):
            self._turn_log(
                "done",
                f"turn {turn} done — {escape(event.lane)}, {_milliseconds(event.total_ms)}: "
                f"{escape(event.reply_text)}",
            )
        elif isinstance(event, TurnError):
            self._turn_log(
                "error",
                f"turn {turn} failed at {escape(event.stage)}: {escape(event.message)}",
            )

    def _turn_log(self, level: str, message: str) -> None:
        style = {
            "info": "dim",
            "warning": "yellow",
            "error": "bold red",
            "action": "cyan",
            "done": "green",
        }.get(level, "white")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.query_one("#turns", RichLog).write(f"[dim]{stamp}[/] [{style}]{message}[/]")

    # Actions

    def action_abort_run(self) -> None:
        if self._client.abort():
            self._log("info", "stop requested")
        else:
            self._log("warning", "not connected: there is nothing to stop")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if self._client.send(text):
            self._log("info", f"sent: {text}")
        else:
            self._log("error", "not connected: the message was not sent")

    # Daemon events

    def _on_event(self, event: Event) -> None:
        if isinstance(event, Ready):
            self._agent_state = event.agent_state
            self._model = event.model
            self._render_status()
        elif isinstance(event, AgentStateEvent):
            self._agent_state = event.state
            self._render_status()
        elif isinstance(event, UserMessageEvent):
            self._show_message(event.message_id, "user", event.text)
        elif isinstance(event, MessageDeltaEvent):
            self._stream_delta(event.message_id, event.delta)
        elif isinstance(event, MessageDoneEvent):
            self._finish_message(event.message_id, event.text, event.stopped, event.error)
        elif isinstance(event, ActivityEvent):
            activity = event.activity
            style = ACTIVITY_STYLES.get(activity.status, "white")
            self._log(
                "activity",
                f"[{style}]{escape(activity.kind)}[/] {escape(activity.title)} — "
                f"{escape(activity.status)}",
            )
        elif isinstance(event, AskEvent):
            question = event.ask
            # The desktop app owns this dialog; the TUI only says it is waiting.
            self._log(
                "warning",
                f"the desktop app is asking: {escape(question.title)} "
                "(answer it there; this window cannot)",
            )
        elif isinstance(event, AskResolvedEvent):
            self._log("info", "the desktop app answered a question")
        elif isinstance(event, ToastEvent):
            style = TOAST_STYLES.get(event.level, "white")
            self._log("toast", f"[{style}]{escape(event.message)}[/]")
        elif isinstance(event, ErrorEvent):
            self._log("error", f"{escape(event.message)} ({escape(event.code)})")
        elif isinstance(event, HistoryEvent):
            self._render_history(event)
        elif isinstance(event, EarlierEvent):
            self._log(
                "info",
                f"an earlier page of {len(event.messages)} messages arrived "
                "(this window does not page back)",
            )
        elif isinstance(event, ConversationsEvent):
            self._show_conversations(event)
        elif isinstance(event, QueueEvent):
            self._queue_total = len(event.steering) + len(event.follow_up)
            self._render_status()

    def _show_conversations(self, event: ConversationsEvent) -> None:
        if event.active_id is not None:
            for conversation in event.conversations:
                if conversation.id == event.active_id:
                    self._conversation_title = conversation.title
                    break
        self._render_status()
        if len(event.conversations) > 1:
            self._log("info", f"{len(event.conversations)} conversations exist")

    # Conversation rendering

    def _conversation(self) -> VerticalScroll:
        return self.query_one("#conversation", VerticalScroll)

    def _show_message(self, message_id: str, role: str, text: str) -> None:
        widget = self._message_widgets.get(message_id)
        if widget is None:
            widget = self._add_message(message_id, role, text)
        self._message_bodies[message_id] = text
        widget.update(self._render_message(role, text))

    def _add_message(self, message_id: str, role: str, text: str) -> Static:
        widget = Static(
            self._render_message(role, text),
            classes=f"message message-{role}",
            markup=False,
        )
        self._conversation().mount(widget)
        self._message_widgets[message_id] = widget
        self._message_bodies[message_id] = text
        self._scroll_to_end()
        return widget

    @staticmethod
    def _render_message(role: str, text: str) -> str:
        return f"{ROLE_LABELS.get(role, 'Jarvis')}: {text}"

    def _stream_delta(self, message_id: str, delta: str) -> None:
        body = self._message_bodies.get(message_id, "") + delta
        self._show_message(message_id, "assistant", body)

    def _finish_message(
        self, message_id: str, text: str, stopped: bool, error: str | None
    ) -> None:
        body = text if text else self._message_bodies.get(message_id, "")
        if error:
            body = f"{body}\n[error: {error}]" if body else f"[error: {error}]"
            self._log("error", f"the answer failed: {escape(error)}")
        if stopped:
            self._log("warning", "the answer was stopped")
        self._show_message(message_id, "assistant", body)
        self._scroll_to_end()

    def _render_history(self, event: HistoryEvent) -> None:
        for widget in self._message_widgets.values():
            widget.remove()
        self._message_widgets.clear()
        self._message_bodies.clear()
        for message in event.messages:
            self._add_message(message.id, message.role, message.text)
            for activity in message.activities:
                style = ACTIVITY_STYLES.get(activity.status, "white")
                self._log(
                    "activity",
                    f"[{style}]{escape(activity.kind)}[/] {escape(activity.title)} — "
                    f"{escape(activity.status)}",
                )
        if event.has_earlier:
            self._log("info", "older messages exist (this window shows the latest page)")
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        self._conversation().scroll_end(animate=False)
        self.call_after_refresh(self._conversation().scroll_end, animate=False)

    # Action log

    def _log(self, level: str, message: str) -> None:
        style = {
            "info": "dim",
            "warning": "yellow",
            "error": "bold red",
            "activity": "white",
            "toast": "white",
        }.get(level, "white")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.query_one("#actions", RichLog).write(f"[dim]{stamp}[/] [{style}]{message}[/]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.tui",
        description=(
            "Host TUI for the Gentle Dot daemon (protocol v1) and the Jarvis backend "
            "(ESP32 state and voice turns)."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="daemon host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="daemon port (default: %(default)s)")
    parser.add_argument("--url", default=None, help="full WebSocket URL; overrides --host and --port")
    parser.add_argument(
        "--token-file",
        default=None,
        help=f"access token file (default: {DEFAULT_TOKEN_PATH})",
    )
    parser.add_argument(
        "--backend-url",
        default=DEFAULT_BACKEND_URL,
        help="Jarvis backend WebSocket URL, without the token (default: %(default)s)",
    )
    parser.add_argument(
        "--backend-token",
        default=None,
        help=(
            f"Jarvis backend token (default: the {TOKEN_ENV} environment variable, "
            f"or {DEFAULT_BACKEND_TOKEN})"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="critical",
        choices=["critical", "error", "warning", "info", "debug"],
        help="log to stderr (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.CRITICAL),
        stream=sys.stderr,
    )
    token_path = Path(args.token_file).expanduser() if args.token_file else None
    JarvisApp(
        host=args.host,
        port=args.port,
        url=args.url,
        token_path=token_path,
        backend_url=args.backend_url,
        backend_token=args.backend_token,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
