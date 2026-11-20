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

Typing ``/login`` opens the sign-in panel (``jarvis.loginview``) above the input, in
both views: the person picks an account and a way to sign in, and answers the daemon's
prompts in the same input, hidden for a ``secret``. Esc cancels. The answers go
straight to the daemon and are never logged, stored or drawn.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
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
)
from .client import (
    AUTH_ERRORS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TOKEN_PATH,
    AskEvent,
    AuthDoneEvent,
    AuthFlowEvent,
    AuthPromptEvent,
    AuthProvider,
    AuthProvidersEvent,
    ConnectionStatus,
    ErrorEvent,
    Event,
    JarvisClient,
    ToastEvent,
)
from .devview import DeveloperView, footer_row
from .loginview import LoginPanel
from .model import LOGIN_CANCELLED, HostModel, Login, LoginStage
from .theme import DEFAULT_DARK, LIGHT, THEMES, Theme, to_text
from .userview import UserView

logger = logging.getLogger(__name__)

TOAST_SEVERITY = {"info": "information", "warning": "warning", "error": "error"}
#: What the input asks for, by place in the ``/login`` panel.
PLACEHOLDER = "Message Jarvis"
LOGIN_PLACEHOLDERS = {
    LoginStage.PROVIDERS: "Number of the account",
    LoginStage.METHOD: "Number of the way to sign in",
    LoginStage.RUNNING: "Waiting for Gentle Dot…",
}
PROMPT_PLACEHOLDERS = {
    "select": "Number of the option",
    "secret": "Hidden as you type",
    "manual_code": "Paste the code or the full address here",
}


def is_login(text: str) -> bool:
    """``/login`` as the daemon itself reads it (upstream ``bridge.ts``)."""

    return text.strip().lower() == "/login"


def footer_keys(login: Login | None) -> tuple[tuple[str, str], ...]:
    """The keys of the footer; the panel's own keys while ``/login`` is open."""

    shared = (("ctrl+q", "quit"), ("f2", "view"), ("f3", "mode"))
    if login is None or not login.open:
        return (("enter", "send"), ("ctrl+a", "stop")) + shared
    if login.running:
        keys: tuple[tuple[str, str], ...] = (("enter", "send"), ("esc", "cancel"))
        if login.link:
            keys += (("ctrl+y", "copy link"),)
        return keys + shared
    back = "back" if login.stage is LoginStage.METHOD else "close"
    return (("enter", "choose"), ("esc", back)) + shared

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
        self.login: Login | None = None

    def render(self) -> Text:
        # Short hints, the same in both views, so the legend keeps its room.
        keys = footer_keys(self.login)
        return to_text([footer_row(self.size.width, keys, self.developer)], self.palette)



class PromptInput(Input):
    """The input row. While it hides a secret, copy and cut do nothing."""

    def action_copy(self) -> None:
        if not self.password:
            super().action_copy()

    def action_cut(self) -> None:
        if not self.password:
            super().action_cut()


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
        Binding("escape", "login_escape", "Close sign-in", show=False, priority=True),
        Binding("ctrl+y", "login_copy", "Copy sign-in link", show=False, priority=True),
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
    ) -> None:
        super().__init__()
        # What both views show; every event goes through it.
        self._host = HostModel()
        #: Animation of the user view (spinner, pulse, voice meter); off for stable tests.
        self.animate = animate
        self._user = UserView(self._host, animate=animate, id=self.USER_VIEW)
        self._developer = DeveloperView(self._host, id=self.DEVELOPER_VIEW)
        self._login = LoginPanel(self._host, id="login")
        self._footer = KeysFooter(id="keys")
        self._footer.login = self._host.login
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
        #: The prompt and panel state the input was last fitted to (``_sync_login``).
        self._shown_prompt: object | None = None
        self._shown_open = False

    # Layout

    def compose(self) -> ComposeResult:
        # Two views in one window (f2); the input, keys and connections stay shared.
        with ContentSwitcher(id="views", initial=self.USER_VIEW):
            yield self._user
            yield self._developer
        yield self._login
        with Horizontal(id="input-row"):
            yield Static("▸", id="prompt-mark")
            yield PromptInput(placeholder=PLACEHOLDER, id="prompt", compact=True)
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
        self._sync_login()

    def _sync_login(self) -> None:
        """Show or hide the ``/login`` panel and fit the input to what it asks."""

        login = self._host.login
        self._login.display = login.open
        self._login.refresh(layout=True)
        try:
            prompt = self.query_one("#prompt", Input)
        except Exception:  # noqa: BLE001 - not composed yet
            return
        asked = login.prompt if login.open and login.running else None
        if asked is not self._shown_prompt or login.open != self._shown_open:
            # A prompt opened or closed (an answer, Esc, auth_done, a refusal, a dropped
            # link, the panel closing): what was typed for it goes, and it goes before
            # the input stops hiding it, so a half-typed secret is never drawn or sent.
            prompt.value = ""
        self._shown_prompt = asked
        self._shown_open = login.open
        # Hidden while a secret is asked, and only then.
        prompt.password = asked is not None and asked.kind == "secret"
        if not login.open:
            prompt.placeholder = PLACEHOLDER
        elif asked is not None:
            prompt.placeholder = asked.placeholder or PROMPT_PLACEHOLDERS.get(asked.kind, "")
        else:
            prompt.placeholder = LOGIN_PLACEHOLDERS[login.stage]

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
        for view in (self._user, self._developer, self._footer, self._login):
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

    def _on_backend_status(self, status: ConnectionStatus, detail: str) -> None:
        if self._closing:
            return
        self._host.set_backend_status(status)
        self._refresh_views()

    # Events

    def _on_backend_event(self, event: BackendEvent) -> None:
        self._host.apply_backend(event)
        self._refresh_views()

    def _on_event(self, event: Event) -> None:
        login = self._host.login
        was_open = login.open
        if isinstance(event, (AuthPromptEvent, AuthFlowEvent)) and login.cancel_next:
            # The panel was left before the daemon named a flow: the first flow named that
            # this sign-in has not taken as its own is that one. End it, even while a new
            # sign-in waits (the daemon answers that one ``auth_busy`` until it ends).
            flow_id = event.prompt.flow_id if isinstance(event, AuthPromptEvent) else event.flow_id
            if flow_id and flow_id != login.flow_id and flow_id not in login.ignored:
                self._client.auth_reply(flow_id, cancelled=True)
                login.ignored.add(flow_id)
                login.cancel_next = False
        self._host.apply_daemon(event)
        if (
            isinstance(event, (AuthPromptEvent, AuthFlowEvent))
            and login.cancel_pending
            and login.flow_id is not None
        ):
            # Esc came before the daemon named the flow: cancel it now.
            self._client.auth_reply(login.flow_id, cancelled=True)
            login.cancel_pending = False
            login.cancelling = True
            login.prompt = None
        if isinstance(event, AuthDoneEvent):
            self._client.auth_list()  # the accounts changed: refresh connected / not connected
            if event.ok and not login.open:
                self.notify(login.result[1] if login.result else "Signed in.", severity="information")
        self._refresh_views()
        if isinstance(event, ErrorEvent) and event.code in AUTH_ERRORS and was_open:
            return  # the panel shows the daemon's refusal
        if isinstance(event, AuthProvidersEvent | AuthPromptEvent | AuthFlowEvent | AuthDoneEvent):
            return
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

    # Actions

    def action_abort_run(self) -> None:
        if not self._client.abort():
            self.notify("Nothing to stop: Gentle Dot is not connected.", severity="warning")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        login = self._host.login
        asked = login.prompt if login.open and login.running else None
        if event.input.password and (asked is None or asked.kind != "secret"):
            # Typed hidden for a prompt that is gone: never an answer, never a message.
            event.input.value = ""
            self._refresh_views()
            return
        if login.open:
            value = event.value
            # Cleared at once: what was typed (maybe a secret) does not stay in the input.
            event.input.value = ""
            self._login_answer(value)
            self._refresh_views()
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if is_login(text):
            self._open_login()
            return
        if not self._client.send(text):
            self.notify("Gentle Dot is not connected: the message was not sent.", severity="error")

    # Sign-in (/login)

    def _open_login(self) -> None:
        if not self._client.auth_list():
            self.notify("Gentle Dot is not connected: /login needs it.", severity="error")
            return
        self._host.login.show()
        self._refresh_views()

    @staticmethod
    def _pick(text: str, count: int) -> int | None:
        """The 0-based choice typed as ``1``..``count``, or ``None``."""

        text = text.strip()
        if text.isdigit() and 1 <= int(text) <= count:
            return int(text) - 1
        return None

    def _login_answer(self, value: str) -> None:
        """One enter in the panel. ``value`` may be a secret: it is sent, never kept."""

        login = self._host.login
        if login.stage is LoginStage.PROVIDERS:
            choice = self._pick(value, len(login.providers))
            if choice is None:
                login.hint = f"Type a number from 1 to {len(login.providers)}." if login.providers else None
                return
            provider = login.providers[choice]
            if len(provider.methods) == 1:
                self._start_login(provider.methods[0], provider)
            elif provider.methods:
                login.provider = provider
                login.stage = LoginStage.METHOD
                login.result = None
                login.hint = None
            return
        if login.stage is LoginStage.METHOD:
            provider = login.provider
            methods = provider.methods if provider else ()
            choice = self._pick(value, len(methods))
            if provider is None or choice is None:
                login.hint = f"Type a number from 1 to {len(methods)}."
                return
            self._start_login(methods[choice], provider)
            return
        prompt = login.prompt
        if prompt is None or login.flow_id is None:
            return  # nothing asked yet
        if prompt.kind == "select":
            choice = self._pick(value, len(prompt.options))
            if choice is None:
                login.hint = f"Type a number from 1 to {len(prompt.options)}."
                return
            answer = prompt.options[choice].id
        else:
            answer = value if prompt.kind == "secret" else value.strip()
            if not answer.strip() and not prompt.optional:
                login.hint = "Type an answer, or press esc to cancel."
                return
        if self._client.auth_reply(login.flow_id, answer):
            self._host.answered_login_prompt()
        else:
            login.hint = "Gentle Dot is not connected: the answer was not sent."

    def _start_login(self, method: str, provider: AuthProvider) -> None:
        login = self._host.login
        if not self._client.auth_login(provider.id, method):
            login.hint = "Gentle Dot is not connected: the sign-in did not start."
            return
        login.start(provider, method)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Esc and ctrl+y belong to the panel only; otherwise the keys go where they went.
        if action == "login_copy" and self._prompt_hidden():
            return False  # nothing is copied while a secret is being typed
        if action in ("login_escape", "login_copy"):
            return self._host.login.open
        return True

    def _prompt_hidden(self) -> bool:
        try:
            return self.query_one("#prompt", Input).password
        except Exception:  # noqa: BLE001 - not composed yet
            return False

    def action_login_escape(self) -> None:
        login = self._host.login
        if not login.open:
            return
        if login.running and (login.cancelling or login.cancel_pending):
            # The cancel went (or waits for the flow's name): Esc again always closes.
            pending = login.cancel_pending
            if login.flow_id is not None:
                login.ignored.add(login.flow_id)
            login.finish(False, LOGIN_CANCELLED)
            login.cancel_next = pending
            login.close()
        elif login.running:
            if login.flow_id is not None:
                self._client.auth_reply(login.flow_id, cancelled=True)
                login.cancelling = True
            else:
                login.cancel_pending = True
            login.prompt = None
        elif login.stage is LoginStage.METHOD:
            login.show()
        else:
            login.close()
        self._refresh_views()

    def action_login_copy(self) -> None:
        link = self._host.login.link if self._host.login.open else None
        if link is None:
            return
        self.copy_to_clipboard(link)
        self.notify("Sign-in link copied.", severity="information")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.tui",
        description=(
            "Host TUI for the Gentle Dot daemon (protocol v1) and the Jarvis backend "
            "(ESP32 state and voice turns). Keys: f2 switches the user and developer "
            "views, f3 switches dark and light mode. Type /login to sign in to an AI account "
            "without leaving the window."
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
        "--log-level",
        default="critical",
        choices=["critical", "error", "warning", "info", "debug"],
        help="log to stderr (default: %(default)s)",
    )
    return parser.parse_args(argv)


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
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
