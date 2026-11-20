# Pyright: "textual" is installed from host/requirements.txt, so a checker running
# without it cannot resolve the import; that is not a defect in this module.
# pyright: reportMissingImports=false

"""Textual front end for the Gentle Dot daemon and the Jarvis backend.

The window shows the conversation, the agent's state and what the agent did on the
computer, all from the Gentle Dot daemon. It sends what the user types and can stop a
run. Confirmations come from the ``jarvis-input`` helper (``confirm`` events, S13) and
from the daemon's ``ask`` method ``confirm`` (other extensions): one card above the
input, ``y`` allows, ``n`` or Esc denies, nothing else answers, and no answer by the
asker's timeout (or a lost connection) is a denial. The daemon's other methods are
pointed to Gentle Dot.

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

f4 is push-to-talk (``jarvis.voice``): press it to record the microphone, press it again
to transcribe with the local whisper-server and send the text like a typed prompt,
shown as said. Esc drops the recording. It never records while ``/login`` is open.

PC control (S13, ``jarvis.control``): the ``jarvis-input`` helper holds the permission;
this window asks it over ``$XDG_RUNTIME_DIR/jarvis-control/control.sock``. ``/control on``
(or f5) grants 30 minutes, ``/control off`` (or f5 again) revokes, ``/control status`` says
the time left. None of it goes to the agent. While it is on, both views show
``◉ control on · 29:12 left · /control off``, counted down from the helper's
``expires_at``; the helper's ``state`` events are the truth (the stop key, a restart). A
helper that cannot be reached shows ``◌ control unavailable``. The window revokes when it
closes, so control never outlives the window that granted it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
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
    THINKING_LEVELS,
    Ask,
    AskEvent,
    AskResolvedEvent,
    AuthDoneEvent,
    AuthFlowEvent,
    AuthPromptEvent,
    AuthProvider,
    AuthProvidersEvent,
    ConnectionStatus,
    ErrorEvent,
    Event,
    JarvisClient,
    ModelsEvent,
    Ready,
    ToastEvent,
    parse_model_spec,
)
from .control import (
    BACKOFF,
    CONFIRM_TIMEOUT_S,
    OFF,
    USAGE,
    ConfirmRequest,
    ControlError,
    ControlLink,
    ControlState,
    ControlUnavailable,
    parse_control,
    remaining_label,
)
from .control import grant as control_grant
from .control import revoke as control_revoke
from .control import status as control_status
from .controlview import ConfirmCard, ControlBar, PendingConfirm
from .devview import DeveloperView, footer_row, place
from .loginview import LoginPanel
from .model import LOGIN_CANCELLED, HostModel, Login, LoginStage, VoiceInput
from .theme import DEFAULT_DARK, LIGHT, THEMES, Theme, to_text
from .userview import UserView
from .voice import MAX_RECORDING_S, ProcessRecorder, Recorder, Transcriber, VoiceError, WhisperTranscriber

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


def footer_keys(
    login: Login | None, voice: VoiceInput | None = None, confirming: bool = False
) -> tuple[tuple[str, str], ...]:
    """The keys of the footer; the panel's own keys while ``/login`` is open."""

    shared = (("ctrl+q", "quit"), ("f2", "view"), ("f3", "mode"))
    if confirming:
        return (("y", "allow"), ("n", "deny"), ("esc", "deny")) + shared
    if voice is not None and voice.recording:
        return (("f4", "send"), ("esc", "cancel")) + shared
    if voice is not None and voice.transcribing:
        return (("esc", "cancel"),) + shared
    if login is None or not login.open:
        return (("enter", "send"), ("ctrl+a", "stop"), ("f4", "talk")) + shared
    if login.running:
        keys: tuple[tuple[str, str], ...] = (("enter", "send"), ("esc", "cancel"))
        if login.link:
            keys += (("ctrl+y", "copy link"),)
        return keys + shared
    back = "back" if login.stage is LoginStage.METHOD else "close"
    return (("enter", "choose"), ("esc", back)) + shared

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
        self.login: Login | None = None
        self.voice: VoiceInput | None = None
        self.confirming = False

    def render(self) -> Text:
        # Short hints, the same in both views, so the legend keeps its room.
        keys = footer_keys(self.login, self.voice, self.confirming)
        if ("f4", "talk") in keys:
            at = keys.index(("f4", "talk")) + 1
            keys = keys[:at] + (("f5", "control"),) + keys[at:]
        # f4 talk and f5 control give way to the developer legend; while recording f4 is the main key.
        optional = ("f4", "f5") if self.voice is None or not self.voice.active else ()
        return to_text([footer_row(self.size.width, keys, self.developer, optional)], self.palette)


def voice_row(voice: VoiceInput, w: int) -> list[tuple[str, str]]:
    """The one row of push-to-talk: recording with its time, or transcribing."""

    sep = ("  ·  ", "mu")
    if voice.recording:
        segs = [("●", "er b"), (f" recording {voice.elapsed_s():.1f}s", "t1 b"), sep,
                ("f4", "t2 b"), (" to send", "mu"), sep, ("esc", "t2 b"), (" cancels", "mu")]
    else:
        segs = [("◐", "lv b"), (" transcribing…", "lv"), sep, ("esc", "t2 b"), (" cancels", "mu")]
    return place([(2, segs)], w, "bc")


class VoiceBar(Widget):
    """Push-to-talk's state above the input, in both views; hidden while idle."""

    DEFAULT_CSS = """
    VoiceBar {
        height: 1;
        background: $jarvis-background;
    }
    """

    def __init__(self, voice: VoiceInput, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.voice = voice
        self.palette: Theme = THEMES[DEFAULT_DARK]

    def render(self) -> Text:
        if not self.voice.active:
            return Text("")
        return to_text([voice_row(self.voice, self.size.width)], self.palette)



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
        Binding("f4", "voice", "Talk", priority=True),
        Binding("f5", "control_toggle", "PC control", priority=True),
        # Only while a confirmation card is up (``check_action``); otherwise they type.
        Binding("y", "confirm_allow", "Allow", show=False, priority=True),
        Binding("n", "confirm_deny", "Deny", show=False, priority=True),
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
        recorder_factory: Callable[[Path], Recorder] | None = None,
        transcriber: Transcriber | None = None,
        max_recording_s: float = MAX_RECORDING_S,
        control_clock: Callable[[], datetime] | None = None,
        confirm_arm_s: float = 0.4,
        control_backoff: tuple[float, float] = BACKOFF,
    ) -> None:
        super().__init__()
        # PC control (/control, f5): what jarvis-input reports, and the countdown to it.
        self._control_clock: Callable[[], datetime] = control_clock or (lambda: datetime.now(UTC))
        self._control_state: ControlState = OFF
        #: The helper could not be reached after the person asked, or the link dropped.
        self._control_unavailable = False
        #: A ``revoke`` of ours is on its way: its ``state`` event is no surprise.
        self._control_expect_off = False
        self._control_tasks: set[asyncio.Task[None]] = set()
        self._control_link = ControlLink(
            on_state=self._on_control_state,
            on_confirm=self._on_helper_confirm,
            on_connection=self._on_control_link,
            backoff=control_backoff,
        )
        self._control_link_task: asyncio.Task[None] | None = None
        self._control_link_up = False
        self._control_bar = ControlBar(self._control_clock, id="control")
        self._control_bar.display = False
        # Confirmations (helper ``confirm``, daemon ``ask`` ``confirm``), oldest first; one card at a time.
        self._confirm_arm_s = confirm_arm_s
        self._confirm_card = ConfirmCard(time.monotonic, id="confirm")
        self._confirm_card.display = False
        self._confirms: list[PendingConfirm] = self._confirm_card.pending
        self._shown_confirm = False
        #: The model and thinking level wanted for typed prompts, applied on connect
        #: only when the daemon runs something else; ``None``: leave the daemon's choice.
        self._wanted_model = parse_model_spec(typed_model) if typed_model else None
        if typed_thinking is not None and typed_thinking not in THINKING_LEVELS:
            raise ValueError(f"unknown thinking level {typed_thinking!r}")
        self._wanted_thinking = typed_thinking
        self._wanted_checked = False
        # What both views show; every event goes through it.
        self._host = HostModel()
        #: Animation of the user view (spinner, pulse, voice meter); off for stable tests.
        self.animate = animate
        self._user = UserView(self._host, animate=animate, id=self.USER_VIEW)
        self._developer = DeveloperView(self._host, id=self.DEVELOPER_VIEW)
        self._login = LoginPanel(self._host, id="login")
        self._footer = KeysFooter(id="keys")
        self._footer.login = self._host.login
        self._footer.voice = self._host.voice
        # Push-to-talk (f4): what records, what transcribes, and the open recording.
        self._voice_bar = VoiceBar(self._host.voice, id="voice")
        self._voice_bar.display = False
        self._recorder_factory: Callable[[Path], Recorder] = recorder_factory or ProcessRecorder
        self._transcriber: Transcriber = transcriber or WhisperTranscriber()
        self._max_recording_s = max_recording_s
        self._recorder: Recorder | None = None
        self._voice_dir: Path | None = None
        #: Bumped by every start and cancel: a late result of an older recording is dropped.
        self._voice_gen = 0
        self._voice_timers: list[object] = []
        self._voice_tasks: set[asyncio.Task[None]] = set()
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
        yield self._confirm_card
        yield self._voice_bar
        yield self._control_bar
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
        self._voice_bar.display = self._host.voice.active
        self._voice_bar.refresh()
        self._control_bar.state = self._control_state
        self._control_bar.unavailable = self._control_unavailable
        self._control_bar.display = self._control_bar.shown
        self._control_bar.refresh()
        self._sync_login()
        self._sync_confirm()

    def _sync_confirm(self) -> None:
        """One modal at a time: while a card waits, the input and ``/login`` panel step aside."""

        confirming = bool(self._confirms)
        self._footer.confirming = confirming
        self._confirm_card.display = confirming
        self._confirm_card.refresh(layout=True)
        if confirming:
            self._login.display = False
        try:
            prompt = self.query_one("#prompt", Input)
        except Exception:  # noqa: BLE001 - not composed yet
            return
        # Off while a card waits: what is typed goes nowhere, never into a message.
        prompt.disabled = confirming
        if self._shown_confirm and not confirming and not self._closing:
            prompt.focus()
        self._shown_confirm = confirming

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
        for view in (self._user, self._developer, self._footer, self._login, self._voice_bar,
                     self._control_bar, self._confirm_card):
            view.palette = THEMES[name]
            view.refresh()

    # Lifetime

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self._client_task = asyncio.create_task(self._client.run(), name="jarvis-client")
        self._client_task.add_done_callback(self._client_finished)
        self._backend_task = asyncio.create_task(self._backend.run(), name="jarvis-backend")
        self._backend_task.add_done_callback(self._backend_finished)
        # The helper's link: its state at connect time (a grant still in force is shown as
        # it is), then its events.
        self._control_link_task = asyncio.create_task(self._control_link.run(), name="jarvis-control")
        self.set_interval(1.0, self._control_tick, name="control-clock")
        self.set_interval(0.25, self._confirm_tick, name="confirm-clock")
        self._refresh_views()

    async def on_unmount(self) -> None:
        # The clients report "not connected" while closing; the widgets are gone by then.
        self._closing = True
        # Control never outlives the window that grants it.
        await self._revoke_on_exit()
        self._user.stop_animation()
        self._cancel_voice()
        for voice_task in list(self._voice_tasks):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(voice_task, 5.0)
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
        if status is not ConnectionStatus.CONNECTED and self._drop_confirms("daemon"):
            # No answer can reach the asker now; its own timeout denies it.
            self.notify("Gentle Dot disconnected: the confirmation was denied.", severity="warning")
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
        if isinstance(event, Ready):
            self._wanted_checked = False  # a new connection checks the wanted model again
        elif isinstance(event, ModelsEvent):
            self._apply_wanted_model(event)
        if isinstance(event, ErrorEvent) and event.code in AUTH_ERRORS and was_open:
            return  # the panel shows the daemon's refusal
        if isinstance(event, AuthProvidersEvent | AuthPromptEvent | AuthFlowEvent | AuthDoneEvent):
            return
        # What the views do not hold is said once, in a notification.
        if isinstance(event, AskEvent):
            self._on_ask(event.ask)
        elif isinstance(event, AskResolvedEvent):
            # Answered elsewhere, or the asker stopped waiting.
            before = len(self._confirms)
            self._confirms[:] = [p for p in self._confirms
                                 if p.source != "daemon" or p.ask.request_id != event.request_id]
            if len(self._confirms) != before:
                self._refresh_views()
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
        if self._confirms:
            return  # the input is off while a card waits; nothing typed is an answer
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
        command = parse_control(text)
        if command is not None:
            self._control_command(command)  # never sent to the agent
            return
        if is_login(text):
            self._open_login()
            return
        if not self._client.send(text):
            self.notify("Gentle Dot is not connected: the message was not sent.", severity="error")
            return
        self._host.note_sent()

    # Sign-in (/login)

    def _open_login(self) -> None:
        if not self._client.auth_list():
            self.notify("Gentle Dot is not connected: /login needs it.", severity="error")
            return
        # Nothing is recorded or sent while signing in.
        if self._cancel_voice():
            self.notify("Recording dropped: nothing is recorded while signing in.", severity="warning")
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
        if action in ("confirm_allow", "confirm_deny"):
            return bool(self._confirms)
        if action == "login_escape":
            return bool(self._confirms) or self._host.login.open or self._host.voice.active
        if action == "login_copy":
            return self._host.login.open
        return True

    def _prompt_hidden(self) -> bool:
        try:
            return self.query_one("#prompt", Input).password
        except Exception:  # noqa: BLE001 - not composed yet
            return False

    def action_login_escape(self) -> None:
        if self._confirms:
            self._answer_confirm(False)  # esc denies; it never allows
            return
        if self._host.voice.active:
            what = "Recording" if self._host.voice.recording else "Transcription"
            self._cancel_voice()
            self.notify(f"{what} cancelled: nothing was sent.", severity="information")
            return
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


    # Push-to-talk (f4)

    async def action_voice(self) -> None:
        """f4: start recording, or stop it and send what was said."""

        voice = self._host.voice
        if self._confirms:
            self.notify("Answer the confirmation first: y allows, n denies.", severity="warning")
            return
        if self._host.login.open:
            self.notify("Close the sign-in panel first: f4 does not record while signing in.", severity="warning")
            return
        if voice.recording:
            self._stop_recording()
        elif not voice.active:
            await self._start_recording()

    async def _start_recording(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="jarvis-voice-"))  # mode 0700
        recorder = self._recorder_factory(directory / "speech.wav")
        try:
            await recorder.start()
        except VoiceError as error:
            shutil.rmtree(directory, ignore_errors=True)
            self.notify(str(error), severity="error")
            return
        if self._host.voice.active or self._host.login.open or self._confirms:  # something else began meanwhile
            self._track(self._discard(recorder, directory))
            return
        self._voice_gen += 1
        self._recorder, self._voice_dir = recorder, directory
        self._host.voice.start()
        self._voice_timers = [
            self.set_interval(0.1, self._voice_bar.refresh, name="voice-clock"),
            self.set_timer(self._max_recording_s, self._recording_capped, name="voice-cap"),
        ]
        self._refresh_views()

    def _recording_capped(self) -> None:
        if self._host.voice.recording:
            self.notify(f"Recording stopped at the {self._max_recording_s:g} s limit.", severity="information")
            self._stop_recording()

    def _stop_timers(self) -> None:
        for timer in self._voice_timers:
            timer.stop()  # type: ignore[attr-defined]
        self._voice_timers = []

    def _track(self, coroutine: object) -> None:
        task = asyncio.create_task(coroutine)  # type: ignore[arg-type]
        self._voice_tasks.add(task)
        task.add_done_callback(self._voice_tasks.discard)

    def _stop_recording(self) -> None:
        recorder, directory = self._recorder, self._voice_dir
        self._recorder = self._voice_dir = None
        self._stop_timers()
        if recorder is None or directory is None:
            self._host.voice.reset()
            self._refresh_views()
            return
        self._host.voice.transcribe()
        self._refresh_views()
        self._track(self._transcribe_and_send(recorder, directory, self._voice_gen))

    async def _transcribe_and_send(self, recorder: Recorder, directory: Path, gen: int) -> None:
        text: str | None = None
        error: str | None = None
        try:
            path = await recorder.stop()
            if gen == self._voice_gen:
                text = await asyncio.to_thread(self._transcriber.transcribe, path)
        except VoiceError as failure:
            error = str(failure)
        except Exception as failure:  # noqa: BLE001 - said to the person, never a crash
            logger.warning("push-to-talk failed", exc_info=failure)
            error = f"Push-to-talk failed: {failure}"
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if gen != self._voice_gen or self._closing:
            return  # cancelled meanwhile (esc, /login, closing): nothing is sent
        self._host.voice.reset()
        self._refresh_views()
        if error is not None:
            self.notify(f"{error.rstrip('.')}. Nothing was sent.", severity="error")
        elif not text or not text.strip():
            self.notify("Nothing was heard: nothing was sent.", severity="warning")
        elif self._host.login.open:
            self.notify("Not sent: the sign-in panel is open.", severity="warning")
        elif not self._client.send(text.strip()):
            self.notify("Gentle Dot is not connected: what you said was not sent.", severity="error")
        else:
            self._host.note_sent(spoken=True)

    def _cancel_voice(self) -> bool:
        """Drop the recording or the transcription under way; ``True`` if there was one."""

        if not self._host.voice.active:
            return False
        self._voice_gen += 1
        self._stop_timers()
        recorder, directory = self._recorder, self._voice_dir
        self._recorder = self._voice_dir = None
        self._host.voice.reset()
        if recorder is not None and directory is not None:
            self._track(self._discard(recorder, directory))
        if not self._closing:
            self._refresh_views()
        return True

    @staticmethod
    async def _discard(recorder: Recorder, directory: Path) -> None:
        try:
            await recorder.cancel()
        except Exception:  # noqa: BLE001 - the file goes either way
            logger.warning("the recorder did not cancel cleanly", exc_info=True)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


    # PC control (/control, f5): asked of jarvis-input

    def action_control_toggle(self) -> None:
        self._control_command("off" if self._control_on() else "on")

    def _control_on(self) -> bool:
        return self._control_state.remaining_s(self._control_clock()) > 0

    def _control_command(self, command: str) -> None:
        if command == "usage":
            self.notify(f"Usage: {USAGE}", severity="information")
            return
        task = asyncio.create_task(self._control_request(command), name=f"control-{command}")
        self._control_tasks.add(task)
        task.add_done_callback(self._control_tasks.discard)

    async def _control_request(self, command: str) -> None:
        try:
            if command == "on":
                self._control_expect_off = False
                state = await control_grant()
                self._control_unavailable = False
                self._control_state = state
                self.notify(
                    "Control on for 30 min: Jarvis may type, press keys and take screenshots. "
                    "/control off or Alt+Shift+Esc stops it.",
                    severity="warning",
                )
            elif command == "off":
                self._control_expect_off = True
                await control_revoke()
                self._control_unavailable = False
                self._control_state = OFF
                self.notify("Control off.", severity="information")
            else:
                state = await control_status()
                self._control_unavailable = False
                self._control_state = state
                now = self._control_clock()
                if state.remaining_s(now) <= 0:
                    self.notify("Control is off. /control on grants it for 30 min.", severity="information")
                else:
                    self.notify(f"Control on · {remaining_label(state.remaining_s(now))} left · /control off",
                                severity="information")
        except ControlUnavailable as error:
            if self._closing:
                return
            self._control_unavailable = True
            self._control_state = OFF
            self.notify(str(error), severity="error")
        except ControlError as error:
            if not self._closing:
                self.notify(str(error), severity="error")
        if not self._closing:
            self._refresh_views()

    def _on_control_state(self, state: ControlState) -> None:
        """A ``state`` event (or the state at connect time): the helper is the truth."""

        if self._closing:
            return
        was_on = self._control_on()
        self._control_state = state
        now_on = self._control_on()
        if now_on:
            self._control_expect_off = False
        elif was_on:
            if not self._control_expect_off:
                self.notify("Control off: jarvis-input revoked it (stop key or /control off elsewhere).",
                            severity="warning")
            self._control_expect_off = False
        self._refresh_views()

    def _on_control_link(self, up: bool, detail: str) -> None:
        if self._closing:
            return
        if up:
            self._control_link_up = True
            self._control_unavailable = False
        else:
            # Reported only after a link that was up dropped, or after the person asked.
            if self._control_link_up:
                self._control_unavailable = True
            self._control_link_up = False
            if self._control_on():
                self.notify("Control off: jarvis-input stopped, and a restart revokes control.",
                            severity="warning")
            self._control_state = OFF
            if self._drop_confirms("helper"):
                self.notify("jarvis-input disconnected: the confirmation was denied.", severity="warning")
            logger.info("control link down: %s", detail)
        self._refresh_views()

    def _control_tick(self) -> None:
        """Every second while on: count down the helper's ``expires_at``; nothing is revoked here."""

        state = self._control_state
        if not state.enabled:
            return
        if state.remaining_s(self._control_clock()) <= 0:
            self._control_state = OFF
            self.notify("control expired · /control on grants 30 min more", severity="warning")
        self._refresh_views()

    async def _revoke_on_exit(self) -> None:
        for task in list(self._control_tasks):
            task.cancel()
        await self._control_link.close()
        if self._control_link_task is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self._control_link_task, 1.0)
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(control_revoke(), 1.0)
        self._control_state = OFF

    # Confirmations (helper ``confirm`` events, daemon ``ask`` ``confirm``)

    def _on_ask(self, ask: Ask) -> None:
        if ask.method != "confirm" or not ask.request_id:
            # Not one this window may answer: say where it waits, and never hang on it.
            self.notify(
                f"Gentle Dot is asking: {ask.title or 'a question'}. Answer this in Gentle Dot.",
                severity="warning",
            )
            return
        timeout_s = ask.timeout_ms / 1000 if ask.timeout_ms else CONFIRM_TIMEOUT_S
        self._add_confirm(ask, timeout_s, "daemon", ask.request_id)

    def _on_helper_confirm(self, request: ConfirmRequest) -> None:
        if self._closing:
            return
        ask = Ask(str(request.id), "confirm", request.title, request.message or None,
                  int(request.timeout_s * 1000))
        self._add_confirm(ask, request.timeout_s, "helper", request.id)

    def _add_confirm(self, ask: Ask, timeout_s: float, source: str, reply_id: object) -> None:
        if any(p.source == source and p.reply_id == reply_id for p in self._confirms):
            return
        if self._cancel_voice():
            self.notify("Recording dropped: answer the confirmation first.", severity="warning")
        now = time.monotonic()
        self._confirms.append(PendingConfirm(ask, now + timeout_s, now + self._confirm_arm_s, source, reply_id))
        self.bell()
        self._refresh_views()

    def _drop_confirms(self, source: str) -> bool:
        """Clear the cards of one asker whose link is gone; ``True`` if any was up."""

        before = len(self._confirms)
        self._confirms[:] = [p for p in self._confirms if p.source != source]
        return len(self._confirms) != before

    def _reply(self, pending: PendingConfirm, allowed: bool) -> bool:
        if pending.source == "helper":
            return self._control_link.answer(pending.reply_id, allowed)  # type: ignore[arg-type]
        return self._client.ui_response(pending.ask.request_id, confirmed=allowed)

    def action_confirm_allow(self) -> None:
        self._answer_confirm(True)

    def action_confirm_deny(self) -> None:
        self._answer_confirm(False)

    def _answer_confirm(self, allowed: bool) -> None:
        if not self._confirms:
            return
        pending = self._confirms[0]
        if time.monotonic() < pending.armed_at:
            return  # typed as the card appeared: not an answer
        self._confirms.pop(0)
        if not self._reply(pending, allowed):
            who = "jarvis-input" if pending.source == "helper" else "Gentle Dot"
            self.notify(f"{who} is not connected: the action stays denied.", severity="warning")
        elif allowed:
            self.notify(f"Allowed: {pending.ask.title}", severity="information")
        else:
            self.notify(f"Denied: {pending.ask.title}", severity="information")
        self._refresh_views()

    def _confirm_tick(self) -> None:
        if not self._confirms:
            return
        now = time.monotonic()
        for pending in [p for p in self._confirms if now >= p.deadline]:
            self._confirms.remove(pending)
            self._reply(pending, False)
            self.notify(f"Confirmation timed out: denied ({pending.ask.title}).", severity="warning")
        self._refresh_views()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.tui",
        description=(
            "Host TUI for the Gentle Dot daemon (protocol v1) and the Jarvis backend "
            "(ESP32 state and voice turns). Keys: f2 switches the user and developer "
            "views, f3 switches dark and light mode, f4 records what you say and sends it "
            "(needs a local whisper-server, see JARVIS_STT_URL), f5 turns PC control on or off. "
            "Type /login to sign in to an AI account without leaving the window, and "
            "/control on|off|status to let the agent use the PC for 30 minutes (y/n answer its "
            "confirmations)."
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
