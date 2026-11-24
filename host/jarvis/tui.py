# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""Textual front end for the Gentle Dot daemon and the Jarvis backend.

The window shows the conversation, the agent's state and what the agent did on the
computer, all from the Gentle Dot daemon. It sends what the user types and can stop a
run; it never answers an ``ask`` approval, because those belong to the desktop app
that owns the private channel to the daemon (S4).

A second, read-only connection to the Jarvis backend (contract C2) adds the state of
the ESP32 and the progress of each voice turn. The two connections are independent:
either one can be down without hiding the other.

Both feed one ``HostModel``, read by two views in the same window: the user view
(``userview``, the default) and the developer view (``devview``), switched with f2.
Both use one colour theme (``jarvis.theme``): f3 switches between the dark and the
light theme, ``--theme`` starts in any of the six.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.theme import Theme as TextualTheme
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Input, Static

from .backend import (
    DEFAULT_BACKEND_TOKEN,
    DEFAULT_BACKEND_URL,
    TOKEN_ENV,
    BackendClient,
    BackendEvent,
    TurnStage,
)
from .client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TOKEN_PATH,
    THINKING_LEVELS,
    AskEvent,
    ConnectionStatus,
    ErrorEvent,
    Event,
    JarvisClient,
    ModelsEvent,
    Ready,
    ToastEvent,
    parse_model_spec,
)
from .devview import DeveloperView, footer_row
from .model import HostModel
from .theme import DEFAULT_DARK, LIGHT, THEMES, Theme, to_text
from .userview import UserView

logger = logging.getLogger(__name__)

TOAST_SEVERITY = {"info": "information", "warning": "warning", "error": "error"}

#: Environment defaults of ``--model`` (``provider/model-id``) and ``--thinking``.
MODEL_ENV = "JARVIS_TYPED_MODEL"
THINKING_ENV = "JARVIS_TYPED_THINKING"

#: Theme tokens exposed to the stylesheet as ``$jarvis-<token>``.
TOKEN_FIELDS = (
    "background", "surface", "message", "border", "dim", "text", "text2", "muted",
    "accent", "live", "ok", "over", "failed", "over_tint", "failed_tint",
)


def textual_theme(tokens: Theme) -> TextualTheme:
    """The Textual theme of one token set; widgets read ``$jarvis-*`` variables."""

    variables = {f"jarvis-{field.replace('_', '-')}": getattr(tokens, field) for field in TOKEN_FIELDS}
    variables.update(
        {
            "jarvis-gradient-start": tokens.gradient_start,
            "jarvis-gradient-end": tokens.gradient_end,
            "input-cursor-background": tokens.accent,
            "input-cursor-foreground": tokens.background,
            "input-cursor-text-style": "none",
            "input-selection-background": tokens.message,
        }
    )
    return TextualTheme(
        name=f"jarvis-{tokens.name}",
        primary=tokens.accent,
        secondary=tokens.live,
        accent=tokens.accent,
        warning=tokens.over,
        error=tokens.failed,
        success=tokens.ok,
        foreground=tokens.text,
        background=tokens.background,
        surface=tokens.surface,
        panel=tokens.surface,
        boost=tokens.message,
        dark=tokens.dark,
        variables=variables,
    )


class KeysFooter(Widget):
    """The one footer of both views: keys, and the glyph legend in the developer view."""

    DEFAULT_CSS = """
    KeysFooter {
        height: 1;
        background: $jarvis-background;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.palette: Theme = THEMES[DEFAULT_DARK]
        self.developer = False

    def render(self) -> Text:
        keys = (
            ("enter", "send"),
            ("ctrl+a", "stop"),
            ("ctrl+q", "quit"),
            # Short hints, the same in both views, so the legend keeps its room.
            ("f2", "view"),
            ("f3", "mode"),
        )
        return to_text([footer_row(self.size.width, keys, self.developer)], self.palette)



class JarvisApp(App[None]):
    """The host window: user view and developer view over one model."""

    TITLE = "Jarvis — Gentle Dot"
    SUB_TITLE = "host TUI (protocol v1)"
    CSS = """
    Screen {
        layout: vertical;
        background: $jarvis-background;
    }
    #views {
        height: 1fr;
    }
    #input-row {
        height: 1;
        background: $jarvis-surface;
    }
    #prompt-mark {
        width: 4;
        padding: 0 0 0 2;
        color: $jarvis-accent;
        text-style: bold;
        background: $jarvis-surface;
    }
    #prompt, #prompt:focus {
        height: 1;
        border: none;
        padding: 0;
        background: $jarvis-surface;
        background-tint: $jarvis-surface 0%;
        color: $jarvis-text;
    }
    #prompt > .input--placeholder {
        color: $jarvis-muted;
    }
    """
    BINDINGS = (
        Binding("ctrl+a", "abort_run", "Abort", priority=True),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("f2", "toggle_view", "Developer view"),
        Binding("f3", "toggle_mode", "Dark / light"),
    )
    USER_VIEW = "user"
    DEVELOPER_VIEW = "developer"

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        url: str | None = None,
        token_path: Path | None = None,
        backend_url: str = DEFAULT_BACKEND_URL,
        backend_token: str | None = None,
        theme_name: str = DEFAULT_DARK,
        animate: bool = True,
        typed_model: str | None = None,
        typed_thinking: str | None = None,
    ) -> None:
        super().__init__()
        #: The model and thinking level wanted for typed prompts, applied on connect
        #: only when the daemon runs something else; ``None``: leave the daemon's choice.
        self._wanted_model = parse_model_spec(typed_model) if typed_model else None
        if typed_thinking is not None and typed_thinking not in THINKING_LEVELS:
            raise ValueError(f"unknown thinking level {typed_thinking!r}")
        self._wanted_thinking = typed_thinking
        self._wanted_checked = False
        self._stt_cpu_warned = False  # the STT-on-CPU warning is said once per window
        # What both views show; every event goes through it.
        self._host = HostModel()
        #: Animation of the user view (spinner, pulse, voice meter); off for stable tests.
        self.animate = animate
        self._user = UserView(self._host, animate=animate, id=self.USER_VIEW)
        self._developer = DeveloperView(self._host, id=self.DEVELOPER_VIEW)
        self._footer = KeysFooter(id="keys")
        # f3 pairs the starting theme with one of the other mode.
        start = THEMES[theme_name]
        self._dark_name = theme_name if start.dark else DEFAULT_DARK
        self._light_name = LIGHT if start.dark else theme_name
        for tokens in THEMES.values():
            self.register_theme(textual_theme(tokens))
        self._apply_theme(theme_name)
        self._backend = BackendClient(
            on_event=self._on_backend_event,
            on_status=self._on_backend_status,
            url=backend_url,
            token=backend_token,
        )
        self._backend_task: asyncio.Task[None] | None = None
        self._closing = False
        self._client = JarvisClient(
            on_event=self._on_event,
            on_status=self._on_status,
            host=host,
            port=port,
            url=url,
            token_path=token_path,
        )
        self._client_task: asyncio.Task[None] | None = None

    # Layout

    def compose(self) -> ComposeResult:
        # Two views in one window (f2); the input, keys and connections stay shared.
        with ContentSwitcher(id="views", initial=self.USER_VIEW):
            yield self._user
            yield self._developer
        with Horizontal(id="input-row"):
            yield Static("▸", id="prompt-mark")
            yield Input(placeholder="Message Jarvis", id="prompt", compact=True)
        yield self._footer

    def action_toggle_view(self) -> None:
        switcher = self.query_one("#views", ContentSwitcher)
        developer = switcher.current != self.DEVELOPER_VIEW
        switcher.current = self.DEVELOPER_VIEW if developer else self.USER_VIEW
        self.screen.set_class(developer, "-developer")
        self._footer.developer = developer
        self._refresh_views()
        self.query_one("#prompt", Input).focus()

    def _refresh_views(self) -> None:
        for view in (self._user, self._developer, self._footer):
            view.refresh()

    # Theme

    @property
    def palette(self) -> Theme:
        return THEMES[self._theme_name]

    def action_toggle_mode(self) -> None:
        """f3: the dark theme and the light theme, both views at once."""

        self._apply_theme(self._light_name if self.palette.dark else self._dark_name)

    def _apply_theme(self, name: str) -> None:
        self._theme_name = name
        self.theme = f"jarvis-{name}"
        for view in (self._user, self._developer, self._footer):
            view.palette = THEMES[name]
            view.refresh()

    # Lifetime

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self._client_task = asyncio.create_task(self._client.run(), name="jarvis-client")
        self._client_task.add_done_callback(self._client_finished)
        self._backend_task = asyncio.create_task(self._backend.run(), name="jarvis-backend")
        self._backend_task.add_done_callback(self._backend_finished)

    async def on_unmount(self) -> None:
        # The clients report "not connected" while closing; the widgets are gone by then.
        self._closing = True
        self._user.stop_animation()
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
            if not self._closing:
                self.notify("The voice connection stopped; restart this window.", severity="error")

    def _client_finished(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("the daemon client stopped", exc_info=error)
            if not self._closing:
                self.notify("The Gentle Dot connection stopped; restart this window.", severity="error")

    # Connections

    def _on_status(self, status: ConnectionStatus, detail: str) -> None:
        if self._closing:
            return
        self._host.set_daemon_status(status, detail)
        self._refresh_views()
        if status is ConnectionStatus.CONNECTED:
            # Read-only: the developer view shows the model's thinking level, and the
            # reply tells whether a wanted model must be applied.
            self._client.models_list()

    def _on_backend_status(self, status: ConnectionStatus, detail: str) -> None:
        if self._closing:
            return
        self._host.set_backend_status(status)
        self._refresh_views()

    # Events

    def _on_backend_event(self, event: BackendEvent) -> None:
        self._host.apply_backend(event)
        self._refresh_views()
        if isinstance(event, TurnStage) and event.warning and not self._stt_cpu_warned:
            # The backend flags an STT slow enough to be on CPU (~15 s vs ~0.35 s on GPU).
            self._stt_cpu_warned = True
            logger.warning(event.warning)
            self.notify(event.warning, severity="warning", timeout=30)

    def _on_event(self, event: Event) -> None:
        self._host.apply_daemon(event)
        self._refresh_views()
        if isinstance(event, Ready):
            self._wanted_checked = False  # a new connection checks the wanted model again
        elif isinstance(event, ModelsEvent):
            self._apply_wanted_model(event)
        # What the views do not hold is said once, in a notification.
        if isinstance(event, AskEvent):
            # The desktop app owns this dialog; the window only says it is waiting.
            self.notify(
                f"Gentle Dot is asking: {event.ask.title}. Answer it in the desktop app.",
                severity="warning",
            )
        elif isinstance(event, ToastEvent):
            self.notify(event.message, severity=TOAST_SEVERITY.get(event.level, "information"))  # type: ignore[arg-type]
        elif isinstance(event, ErrorEvent):
            self.notify(event.message, severity="error")

    def _apply_wanted_model(self, event: ModelsEvent) -> None:
        """Once per connection: ``model_set`` only when the running model differs."""

        if self._wanted_checked or (self._wanted_model is None and self._wanted_thinking is None):
            return
        current = event.current
        if current is None:
            return
        self._wanted_checked = True
        provider, model_id = self._wanted_model or (current.provider, current.id)
        thinking = self._wanted_thinking
        same_model = (provider, model_id) == (current.provider, current.id)
        if same_model and (thinking is None or thinking == current.thinking):
            return
        logger.info("switching typed model to %s/%s thinking %s", provider, model_id, thinking)
        self._client.model_set(provider, model_id, thinking)

    # Actions

    def action_abort_run(self) -> None:
        if not self._client.abort():
            self.notify("Nothing to stop: Gentle Dot is not connected.", severity="warning")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if not self._client.send(text):
            self.notify("Gentle Dot is not connected: the message was not sent.", severity="error")
            return
        self._host.note_sent()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.tui",
        description=(
            "Host TUI for the Gentle Dot daemon (protocol v1) and the Jarvis backend "
            "(ESP32 state and voice turns). Keys: f2 switches the user and developer "
            "views, f3 switches dark and light mode."
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
        "--theme",
        default=DEFAULT_DARK,
        choices=list(THEMES),
        help=(
            f"colour theme to start in (default: %(default)s; f3 switches to {LIGHT} "
            "and back): %(choices)s"
        ),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get(MODEL_ENV) or None,
        metavar="PROVIDER/ID",
        help=(
            f"model wanted for typed prompts, e.g. openai/gpt-5.4-mini (default: {MODEL_ENV}, "
            "else the daemon's own). Sent as model_set on connect only when the daemon "
            "runs another one; the daemon saves it as its default"
        ),
    )
    parser.add_argument(
        "--thinking",
        default=os.environ.get(THINKING_ENV) or None,
        metavar="LEVEL",
        help=(
            f"thinking level wanted for typed prompts: {', '.join(THINKING_LEVELS)} "
            f"(default: {THINKING_ENV}, else the daemon's own); same rule as --model"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="critical",
        choices=["critical", "error", "warning", "info", "debug"],
        help="log to stderr (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.model is not None:
        try:
            parse_model_spec(args.model)
        except ValueError as error:
            parser.error(f"--model / {MODEL_ENV}: {error}")
    if args.thinking is not None and args.thinking not in THINKING_LEVELS:
        parser.error(
            f"--thinking / {THINKING_ENV}: {args.thinking!r} is not one of {', '.join(THINKING_LEVELS)}"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
        theme_name=args.theme,
        typed_model=args.model,
        typed_thinking=args.thinking,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
