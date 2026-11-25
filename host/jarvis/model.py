"""What the host window knows, shared by every view and free of any UI toolkit.

Both connections feed one ``HostModel``: the Jarvis backend (contract C2: devices and
voice turns) and the Gentle Dot daemon (agent, typed conversation, PC activity). The
views only read it. Figures the backend does not send are derived here: per-stage
medians, lane targets (D17, host constants), over-target amounts and lane verdicts.

Two gaps of contract C2 are bridged without changing it: an ``action`` carries no
``turn_id``, so it joins the open turn of the same device (or no turn at all), and the
lane of a turn is only known at ``turn_done``, so a live turn has ``lane = None``.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from .backend import (
    ActionEvent,
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
    AUTH_ERRORS,
    Activity,
    ActivityEvent,
    AgentStateEvent,
    AuthDoneEvent,
    AuthFlowEvent,
    AuthPrompt,
    AuthPromptEvent,
    AuthProvider,
    AuthProvidersEvent,
    ConnectionStatus,
    ConversationsEvent,
    ErrorEvent,
    Event,
    HistoryEvent,
    MessageDeltaEvent,
    MessageDoneEvent,
    ModelRef,
    ModelsEvent,
    QueueEvent,
    Ready,
    UserMessageEvent,
)

#: The stages of a voice turn, in order (contract C2, ``turn_stage``).
STAGES: Final = ("stt", "route", "agent", "tts")
#: Seconds a turn of each lane should take at most; ``None``: no target (D17).
LANE_TARGETS: Final[dict[str, float | None]] = {"fast": 1.5, "chat": 4.0, "task": None}
#: The PC activity kept for the views, newest first.
MAX_ACTIVITIES: Final = 50
#: ``HostModel.agent_status`` while the daemon link is down.
OFFLINE: Final = "offline"
#: Daemon ``error`` codes that last until the person acts in Gentle Dot; the views keep
#: their message on screen. ``no_account``: the daemon refuses every send until an AI
#: account is connected (upstream f97d56d, observed on the real daemon).
LASTING_ERRORS: Final = frozenset({"no_account"})
#: Under the sign-in notice, unless the daemon's own words already say it.
LOGIN_HINT: Final = "Type /login to connect one."


class StageState(StrEnum):
    WAITING = "waiting"
    LIVE = "live"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class Stage:
    state: StageState = StageState.WAITING
    ms: float | None = None


@dataclass(slots=True)
class Turn:
    """One voice turn, from ``turn_started`` to ``turn_done`` or ``turn_error``."""

    turn_id: str
    device_id: str
    started_at: float
    stages: dict[str, Stage] = field(default_factory=lambda: {s: Stage() for s in STAGES})
    heard: str | None = None
    reply: str | None = None
    lane: str | None = None
    total_ms: float | None = None
    error_stage: str | None = None
    error_message: str | None = None
    actions: list[ActionEvent] = field(default_factory=list)
    done: bool = False

    def stage(self, name: str) -> Stage:
        return self.stages[name]

    @property
    def is_done(self) -> bool:
        return self.done

    @property
    def is_failed(self) -> bool:
        return self.error_stage is not None

    @property
    def is_live(self) -> bool:
        return not self.done and self.error_stage is None

    @property
    def total_s(self) -> float | None:
        return None if self.total_ms is None else self.total_ms / 1000

    @property
    def target_s(self) -> float | None:
        return None if self.lane is None else LANE_TARGETS.get(self.lane)

    @property
    def over_by_s(self) -> float | None:
        """Seconds past the lane target; ``None`` when within it or without one."""

        total, target = self.total_s, self.target_s
        if total is None or target is None or total <= target:
            return None
        return total - target

    @property
    def over_target(self) -> bool:
        return self.over_by_s is not None


@dataclass(slots=True)
class Message:
    message_id: str
    role: str
    text: str
    streaming: bool = False
    #: When the host first saw it; ``None`` for history, which carries no time.
    at: float | None = None
    error: str | None = None
    stopped: bool = False
    #: A user message this window sent from the microphone (f4), not typed.
    spoken: bool = False

    @property
    def answer(self) -> str:
        """The text without the ``[error: …]`` line the developer view shows."""

        if self.error is None:
            return self.text
        suffix = f"[error: {self.error}]"
        return self.text.removesuffix(suffix).removesuffix("\n")


def fmt_seconds(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds:.1f}s"


@dataclass(slots=True)
class PromptTiming:
    """Monotonic stamps of one typed prompt: send, echo, first non-empty text, done.

    A prompt may bring several messages (a tool-using one: an empty message with the
    tool call, then the text), so the first text is the first across all of them and
    the end is the agent going back to ``idle``. Until then ``done_at`` is the last
    ``message_done`` and ``settled`` is false; a stop, an error, the next prompt or a
    dropped link settle it where it is, so a daemon that never says idle does not hang it.

    The daemon's frames carry no time, so these are host-side: they include the
    local WebSocket hop, which is small next to the model's own time.
    """

    sent_at: float
    echo_at: float | None = None
    first_text_at: float | None = None
    done_at: float | None = None
    error: str | None = None
    stopped: bool = False
    settled: bool = False
    #: The newest assistant message of this prompt: the reply the time belongs to.
    reply_id: str | None = None
    #: Sent from the microphone (f4) rather than typed.
    spoken: bool = False

    def _since(self, at: float | None) -> float | None:
        return None if at is None else at - self.sent_at

    @property
    def echo_s(self) -> float | None:
        return self._since(self.echo_at)

    @property
    def first_text_s(self) -> float | None:
        return self._since(self.first_text_at)

    @property
    def total_s(self) -> float | None:
        return self._since(self.done_at)

    def summary(self) -> str:
        """``first text 1.2s · total 3.4s``, as the developer view shows it."""

        return f"first text {fmt_seconds(self.first_text_s)} · total {fmt_seconds(self.total_s)}"


@dataclass(slots=True, frozen=True)
class Counts:
    finished: int
    over_target: int
    failed: int


@dataclass(slots=True, frozen=True)
class LaneStats:
    """Finished turns of one lane. ``verdict``: empty, within, over or no_target."""

    lane: str
    n: int
    median_s: float | None
    worst_s: float | None
    target_s: float | None
    n_over: int
    verdict: str

    @property
    def over_by_s(self) -> float | None:
        if self.worst_s is None or self.target_s is None or self.worst_s <= self.target_s:
            return None
        return self.worst_s - self.target_s


class LoginStage(StrEnum):
    """Where the ``/login`` panel is."""

    PROVIDERS = "providers"  # choose an account
    METHOD = "method"  # choose subscription or API key
    RUNNING = "running"  # a sign-in is under way (waiting, or a prompt is open)


#: Steps of a sign-in kept for the panel, newest last.
MAX_LOGIN_STEPS: Final = 20
#: The sign-in ends when the daemon link drops: its flow does not survive the connection.
LOGIN_DROPPED: Final = "The connection to Gentle Dot dropped: the sign-in ended."
LOGIN_CANCELLED: Final = "Sign-in cancelled."


@dataclass(slots=True)
class Login:
    """The ``/login`` panel. It holds what the daemon said, never what the person typed."""

    open: bool = False
    stage: LoginStage = LoginStage.PROVIDERS
    providers: tuple[AuthProvider, ...] = ()
    #: ``True`` once the daemon answered ``auth_list`` at least once.
    listed: bool = False
    provider: AuthProvider | None = None
    method: str | None = None
    flow_id: str | None = None
    prompt: AuthPrompt | None = None
    steps: list[AuthFlowEvent] = field(default_factory=list)
    #: The outcome of the last sign-in: ``(ok, text)``.
    result: tuple[bool, str] | None = None
    #: A hint about the last answer (for example a number out of range).
    hint: str | None = None
    #: Esc came before the daemon named the flow: cancel it as soon as it does.
    cancel_pending: bool = False
    #: Esc sent ``auth_reply{cancelled}``; the daemon's ``auth_done`` ends it.
    cancelling: bool = False
    #: The panel was left before the daemon named the flow: cancel the next flow it names.
    cancel_next: bool = False
    #: Flows this window cancelled and left: their late frames change nothing.
    ignored: set[str] = field(default_factory=set)

    @property
    def running(self) -> bool:
        return self.stage is LoginStage.RUNNING

    @property
    def link(self) -> str | None:
        """The newest address of this sign-in, for ctrl+y."""

        for step in reversed(self.steps):
            if step.kind == "auth_url" and step.url:
                return step.url
            if step.kind == "device_code" and step.verification_uri:
                return step.verification_uri
        return None

    def show(self) -> None:
        """Open the panel on the list, keeping a sign-in that is under way."""

        self.open = True
        if not self.running:
            self.stage = LoginStage.PROVIDERS
            self.hint = None

    def close(self) -> None:
        self.open = False
        self.hint = None
        if not self.running:
            self.stage = LoginStage.PROVIDERS
            self.result = None

    def start(self, provider: AuthProvider, method: str) -> None:
        self.stage = LoginStage.RUNNING
        self.provider = provider
        self.method = method
        self.flow_id = None
        self.prompt = None
        self.steps = []
        self.result = None
        self.hint = None
        self.cancel_pending = False
        self.cancelling = False

    def finish(self, ok: bool, text: str) -> None:
        self.stage = LoginStage.PROVIDERS
        self.prompt = None
        self.flow_id = None
        self.cancel_pending = False
        self.cancelling = False
        self.result = (ok, text)


class VoiceInput:
    """Push-to-talk in this window (f4): idle, recording or transcribing."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self.phase = "idle"
        self.started_at: float | None = None

    @property
    def recording(self) -> bool:
        return self.phase == "recording"

    @property
    def transcribing(self) -> bool:
        return self.phase == "transcribing"

    @property
    def active(self) -> bool:
        return self.phase != "idle"

    def elapsed_s(self) -> float:
        return 0.0 if self.started_at is None else max(0.0, self._monotonic() - self.started_at)

    def start(self) -> None:
        self.phase = "recording"
        self.started_at = self._monotonic()

    def transcribe(self) -> None:
        self.phase = "transcribing"

    def reset(self) -> None:
        self.phase = "idle"
        self.started_at = None


def _median(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.median(items) if items else None


class HostModel:
    """State of both connections. ``apply_*`` and ``set_*`` change it; views read it."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._monotonic = monotonic
        #: Send stamps of typed prompts not echoed yet, oldest first (the daemon queues),
        #: each with whether it was spoken (f4) rather than typed.
        self._sent: deque[tuple[float, bool]] = deque()
        #: The typed prompt being answered now, and the last one that finished.
        self._timing: PromptTiming | None = None
        self.last_timing: PromptTiming | None = None
        #: Timed replies by assistant message id (typed here, in this window).
        self._reply_timings: dict[str, PromptTiming] = {}
        self.devices: dict[str, Device] = {}
        self.backend_status = ConnectionStatus.DISCONNECTED
        self.daemon_status = ConnectionStatus.DISCONNECTED
        #: Why the daemon link is down, as the client words it for a person.
        self.daemon_detail = ""
        self.agent_state = "starting"
        self.agent_model: str | None = None
        #: The running model and thinking level, from the daemon's ``models`` reply.
        self.agent_model_ref: ModelRef | None = None
        self.agent_thinking: str | None = None
        #: The daemon's own message while it cannot answer until the person acts (for
        #: example, connect an AI account); shown as is, cleared once a prompt runs.
        self.agent_notice: str | None = None
        self.chat_title: str | None = None
        self.queue_total = 0
        #: The ``/login`` panel, shared by both views.
        self.login = Login()
        #: Push-to-talk (f4), shared by both views.
        self.voice = VoiceInput(monotonic)
        self.turns: list[Turn] = []
        self._turns_by_id: dict[str, Turn] = {}
        #: Actions that arrived while their device had no open turn.
        self.loose_actions: list[ActionEvent] = []
        self.messages: list[Message] = []
        self._messages_by_id: dict[str, Message] = {}
        self._activities: dict[str, Activity] = {}
        #: Activity ids per message, in arrival order (the user view nests them).
        self._activity_ids: dict[str, list[str]] = {}

    # Connections

    def set_backend_status(self, status: ConnectionStatus) -> None:
        self.backend_status = status

    def set_daemon_status(self, status: ConnectionStatus, detail: str = "") -> None:
        self.daemon_status = status
        self.daemon_detail = "" if status is ConnectionStatus.CONNECTED else detail
        if status is not ConnectionStatus.CONNECTED:
            self._settle_timing()  # the link dropped: no idle will come for it
            if self.login.running:
                self.login.finish(False, LOGIN_DROPPED)

    @property
    def agent_status(self) -> str:
        """The agent's availability, as both views show it.

        ``offline`` while the daemon link is down (whatever state the agent last
        reported), otherwise the agent's own state (``idle``, ``working``…).
        """

        return self.agent_state if self.daemon_status is ConnectionStatus.CONNECTED else OFFLINE

    @property
    def login_hint(self) -> str | None:
        """How to act on the sign-in notice, when the notice does not say it."""

        if not self.agent_notice or "/login" in self.agent_notice:
            return None
        return LOGIN_HINT

    @property
    def agent_offline_reason(self) -> str | None:
        """Why the agent is offline: ``no_token``, ``refused``, ``mismatch`` or ``not_running``."""

        if self.daemon_status is ConnectionStatus.CONNECTED:
            return None
        if self.daemon_status is ConnectionStatus.UNAUTHORIZED:
            return "refused"
        if self.daemon_status is ConnectionStatus.PROTOCOL_MISMATCH:
            return "mismatch"
        if "access token" in self.daemon_detail:
            return "no_token"
        return "not_running"

    # Backend events

    def apply_backend(self, event: BackendEvent) -> None:
        if isinstance(event, SnapshotEvent):
            self.devices = {device.device_id: device for device in event.devices}
        elif isinstance(event, DeviceEvent):
            self.devices[event.device.device_id] = event.device
        elif isinstance(event, ActionEvent):
            turn = self.open_turn(event.device_id)
            if turn is None:
                self.loose_actions.append(event)
            else:
                turn.actions.append(event)
        elif isinstance(event, TurnStarted):
            turn = self._open(event.turn_id, event.device_id, event.ts)
            turn.stages[STAGES[0]].state = StageState.LIVE
        elif isinstance(event, TurnStage):
            self._stage(self._open(event.turn_id, "unknown", event.ts), event)
        elif isinstance(event, TurnDone):
            turn = self._open(event.turn_id, "unknown", event.ts)
            turn.done = True
            turn.lane = event.lane
            turn.total_ms = event.total_ms
            turn.reply = event.reply_text or turn.reply
            self._settle(turn)
        elif isinstance(event, TurnError):
            turn = self._open(event.turn_id, "unknown", event.ts)
            self._settle(turn)
            turn.error_stage = event.stage or "unknown"
            turn.error_message = event.message
            if event.stage in turn.stages:
                turn.stages[event.stage].state = StageState.FAILED

    def turn(self, turn_id: str) -> Turn | None:
        return self._turns_by_id.get(turn_id)

    def open_turn(self, device_id: str) -> Turn | None:
        """The newest live turn of ``device_id``."""

        for turn in reversed(self.turns):
            if turn.is_live and turn.device_id == device_id:
                return turn
        return None

    def live_turn(self) -> Turn | None:
        """The newest live turn of any device."""

        for turn in reversed(self.turns):
            if turn.is_live:
                return turn
        return None

    def _open(self, turn_id: str, device_id: str, ts: float | None) -> Turn:
        turn = self._turns_by_id.get(turn_id)
        if turn is None:
            turn = Turn(turn_id, device_id, self._clock() if ts is None else ts)
            self.turns.append(turn)
            self._turns_by_id[turn_id] = turn
        return turn

    @staticmethod
    def _stage(turn: Turn, event: TurnStage) -> None:
        if event.stage not in turn.stages:
            return
        index = STAGES.index(event.stage)
        # An earlier stage still marked live was skipped by this lane.
        for name in STAGES[:index]:
            if turn.stages[name].state is StageState.LIVE:
                turn.stages[name].state = StageState.WAITING
        stage = turn.stages[event.stage]
        stage.state = StageState.DONE
        stage.ms = event.ms
        if event.stage == "stt" and event.text:
            turn.heard = event.text
        if event.stage == "agent" and event.text and not turn.reply:
            turn.reply = event.text
        if index + 1 < len(STAGES) and turn.is_live:
            following = turn.stages[STAGES[index + 1]]
            if following.state is StageState.WAITING:
                following.state = StageState.LIVE

    @staticmethod
    def _settle(turn: Turn) -> None:
        for stage in turn.stages.values():
            if stage.state is StageState.LIVE:
                stage.state = StageState.WAITING

    # Derived figures

    def finished_turns(self) -> list[Turn]:
        """Done and failed turns, oldest first."""

        return [turn for turn in self.turns if not turn.is_live]

    def counts(self) -> Counts:
        finished = self.finished_turns()
        return Counts(
            finished=len(finished),
            over_target=sum(1 for turn in finished if turn.over_target),
            failed=sum(1 for turn in finished if turn.is_failed),
        )

    def stage_medians(self) -> dict[str, float | None]:
        """Median ms of each stage over the turns that finished well this session."""

        done = [turn for turn in self.turns if turn.is_done]
        return {
            name: _median(
                turn.stages[name].ms
                for turn in done
                if turn.stages[name].state is StageState.DONE and turn.stages[name].ms is not None
            )
            for name in STAGES
        }

    def lane_stats(self) -> list[LaneStats]:
        out: list[LaneStats] = []
        for lane, target in LANE_TARGETS.items():
            totals = [
                turn.total_s
                for turn in self.turns
                if turn.is_done and turn.lane == lane and turn.total_s is not None
            ]
            n_over = sum(
                1 for turn in self.turns if turn.is_done and turn.lane == lane and turn.over_target
            )
            worst = max(totals) if totals else None
            if not totals:
                verdict = "empty"
            elif target is None:
                verdict = "no_target"
            elif n_over:
                verdict = "over"
            else:
                verdict = "within"
            median = _median(totals)
            out.append(
                LaneStats(
                    lane=lane,
                    n=len(totals),
                    median_s=None if median is None else round(median, 3),
                    worst_s=worst,
                    target_s=target,
                    n_over=n_over,
                    verdict=verdict,
                )
            )
        return out

    # Daemon events

    def note_sent(self, *, spoken: bool = False) -> None:
        """A prompt left this host now; its echo, first text and end are timed.

        ``spoken``: it was said into the microphone (f4) and transcribed, not typed; its
        echo is marked so both views show it as said.
        """

        self._sent.append((self._monotonic(), spoken))

    def apply_daemon(self, event: Event) -> None:
        self._time(event)
        if isinstance(event, Ready):
            self.agent_state = event.agent_state
            self.agent_model = event.model
        elif isinstance(event, ModelsEvent):
            if event.current is not None:
                self.agent_model_ref = event.current
                self.agent_model = event.current.name
                self.agent_thinking = event.current.thinking
        elif isinstance(event, AgentStateEvent):
            self.agent_state = event.state
        elif isinstance(event, ErrorEvent):
            if event.code in LASTING_ERRORS and event.message:
                self.agent_notice = event.message
            elif event.code in AUTH_ERRORS and self.login.running:
                # The daemon refused or no longer knows this sign-in: its own words, as they are.
                self.login.finish(False, event.message or "The sign-in did not start.")
        elif isinstance(event, (AuthProvidersEvent, AuthPromptEvent, AuthFlowEvent, AuthDoneEvent)):
            self._apply_login(event)
        elif isinstance(event, UserMessageEvent):
            self.agent_notice = None  # the daemon started a prompt: it can answer again
            message = self._message(event.message_id, "user", event.text, streaming=False, at=self._clock())
            if self._timing is not None and self._timing.spoken:  # set by _time for this echo
                message.spoken = True
        elif isinstance(event, MessageDeltaEvent):
            self.agent_notice = None
            known = self._messages_by_id.get(event.message_id)
            body = (known.answer if known else "") + event.delta
            self._message(event.message_id, "assistant", body, streaming=True, at=self._clock())
        elif isinstance(event, MessageDoneEvent):
            known = self._messages_by_id.get(event.message_id)
            body = event.text or (known.answer if known else "")
            if event.error:
                body = f"{body}\n[error: {event.error}]" if body else f"[error: {event.error}]"
            message = self._message(event.message_id, "assistant", body, streaming=False, at=self._clock())
            message.error = event.error
            message.stopped = event.stopped
        elif isinstance(event, ActivityEvent):
            self._activity(event.activity, event.message_id)
        elif isinstance(event, HistoryEvent):
            self.messages.clear()
            self._messages_by_id.clear()
            for message in event.messages:
                self._message(message.id, message.role, message.text, streaming=False)
                for activity in message.activities:
                    self._activity(activity, message.id)
        elif isinstance(event, ConversationsEvent):
            if event.active_id is not None:
                for conversation in event.conversations:
                    if conversation.id == event.active_id:
                        self.chat_title = conversation.title
                        break
        elif isinstance(event, QueueEvent):
            self.queue_total = len(event.steering) + len(event.follow_up)

    def _apply_login(
        self, event: AuthProvidersEvent | AuthPromptEvent | AuthFlowEvent | AuthDoneEvent
    ) -> None:
        login = self.login
        if isinstance(event, AuthProvidersEvent):
            login.providers = event.providers
            login.listed = True
            if event.open:
                login.show()
            return
        flow_id = event.prompt.flow_id if isinstance(event, AuthPromptEvent) else event.flow_id
        if flow_id in login.ignored:
            return  # a flow this window cancelled: not this sign-in's outcome
        if isinstance(event, AuthDoneEvent):
            if login.flow_id not in (None, event.flow_id):
                return  # another window's sign-in
            if event.ok:
                self.agent_notice = None  # an account is connected: the daemon answers again
                name = login.provider.name if login.provider else event.provider_id
                login.finish(True, f"Signed in to {name}.")
            else:
                login.finish(False, event.message or "Sign-in did not finish.")
            return
        if not login.running or login.flow_id not in (None, flow_id):
            return
        if isinstance(event, AuthPromptEvent):
            login.flow_id = event.prompt.flow_id
            if login.cancelling:
                return  # being cancelled: nothing more to answer
            login.prompt = event.prompt
            login.hint = None
        else:
            login.flow_id = event.flow_id
            login.steps.append(event)
            del login.steps[:-MAX_LOGIN_STEPS]

    def answered_login_prompt(self) -> None:
        """The open prompt was answered; the answer itself is not kept."""

        self.login.prompt = None
        self.login.hint = None

    def reply_timing(self, message_id: str) -> PromptTiming | None:
        """The time of the typed prompt this assistant message ended, if it was timed here."""

        return self._reply_timings.get(message_id)

    def _settle_timing(self) -> None:
        """End the open timing where it is (its last ``message_done``), if one is open."""

        timing, self._timing = self._timing, None
        if timing is not None and timing.done_at is not None:
            timing.settled = True

    def _time(self, event: Event) -> None:
        if isinstance(event, Ready):
            # A new connection: prompts sent before it will not be echoed to this one.
            self._sent.clear()
            self._settle_timing()
        elif isinstance(event, UserMessageEvent):
            self._settle_timing()  # the agent went on to the next prompt without idle
            # Only prompts typed here are timed; one from another window is not.
            if self._sent:
                sent_at, spoken = self._sent.popleft()
                self._timing = PromptTiming(sent_at, spoken=spoken)
            else:
                self._timing = None
            if self._timing is not None:
                self._timing.echo_at = self._monotonic()
        elif self._timing is None:
            return
        elif isinstance(event, MessageDeltaEvent):
            if event.delta and self._timing.first_text_at is None:
                self._timing.first_text_at = self._monotonic()
        elif isinstance(event, MessageDoneEvent):
            timing = self._timing
            timing.done_at = self._monotonic()
            if timing.first_text_at is None and event.text:
                timing.first_text_at = timing.done_at  # the whole text came at once
            timing.error = event.error
            timing.stopped = event.stopped
            if timing.reply_id is not None:
                self._reply_timings.pop(timing.reply_id, None)
            timing.reply_id = event.message_id
            self._reply_timings[event.message_id] = timing
            self.last_timing = timing  # provisional until idle
            if event.error or event.stopped:
                self._settle_timing()
        elif isinstance(event, AgentStateEvent) and event.state == "idle":
            timing, self._timing = self._timing, None
            timing.done_at = self._monotonic()
            timing.settled = True
            self.last_timing = timing

    @property
    def activities(self) -> list[Activity]:
        """PC activity, newest first."""

        return list(reversed(self._activities.values()))

    def activities_of(self, message_id: str) -> list[Activity]:
        """The PC activity of one message, oldest first."""

        ids = self._activity_ids.get(message_id, [])
        return [self._activities[i] for i in ids if i in self._activities]

    def _message(
        self, message_id: str, role: str, text: str, *, streaming: bool, at: float | None = None
    ) -> Message:
        message = self._messages_by_id.get(message_id)
        if message is None:
            message = Message(message_id, role, text, streaming, at)
            self.messages.append(message)
            self._messages_by_id[message_id] = message
            return message
        message.text = text
        message.streaming = streaming
        return message

    def _activity(self, activity: Activity, message_id: str = "") -> None:
        # An update keeps the place of the activity it updates.
        self._activities[activity.id] = activity
        if message_id:
            ids = self._activity_ids.setdefault(message_id, [])
            if activity.id not in ids:
                ids.append(activity.id)
        while len(self._activities) > MAX_ACTIVITIES:
            self._activities.pop(next(iter(self._activities)))
