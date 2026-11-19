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
    Activity,
    ActivityEvent,
    AgentStateEvent,
    ConnectionStatus,
    ConversationsEvent,
    Event,
    HistoryEvent,
    MessageDeltaEvent,
    MessageDoneEvent,
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


def _median(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.median(items) if items else None


class HostModel:
    """State of both connections. ``apply_*`` and ``set_*`` change it; views read it."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.devices: dict[str, Device] = {}
        self.backend_status = ConnectionStatus.DISCONNECTED
        self.daemon_status = ConnectionStatus.DISCONNECTED
        self.agent_state = "starting"
        self.agent_model: str | None = None
        self.chat_title: str | None = None
        self.queue_total = 0
        self.turns: list[Turn] = []
        self._turns_by_id: dict[str, Turn] = {}
        #: Actions that arrived while their device had no open turn.
        self.loose_actions: list[ActionEvent] = []
        self.messages: list[Message] = []
        self._messages_by_id: dict[str, Message] = {}
        self._activities: dict[str, Activity] = {}

    # Connections

    def set_backend_status(self, status: ConnectionStatus) -> None:
        self.backend_status = status

    def set_daemon_status(self, status: ConnectionStatus) -> None:
        self.daemon_status = status

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

    def apply_daemon(self, event: Event) -> None:
        if isinstance(event, Ready):
            self.agent_state = event.agent_state
            self.agent_model = event.model
        elif isinstance(event, AgentStateEvent):
            self.agent_state = event.state
        elif isinstance(event, UserMessageEvent):
            self._message(event.message_id, "user", event.text, streaming=False)
        elif isinstance(event, MessageDeltaEvent):
            known = self._messages_by_id.get(event.message_id)
            body = (known.text if known else "") + event.delta
            self._message(event.message_id, "assistant", body, streaming=True)
        elif isinstance(event, MessageDoneEvent):
            known = self._messages_by_id.get(event.message_id)
            body = event.text or (known.text if known else "")
            if event.error:
                body = f"{body}\n[error: {event.error}]" if body else f"[error: {event.error}]"
            self._message(event.message_id, "assistant", body, streaming=False)
        elif isinstance(event, ActivityEvent):
            self._activity(event.activity)
        elif isinstance(event, HistoryEvent):
            self.messages.clear()
            self._messages_by_id.clear()
            for message in event.messages:
                self._message(message.id, message.role, message.text, streaming=False)
                for activity in message.activities:
                    self._activity(activity)
        elif isinstance(event, ConversationsEvent):
            if event.active_id is not None:
                for conversation in event.conversations:
                    if conversation.id == event.active_id:
                        self.chat_title = conversation.title
                        break
        elif isinstance(event, QueueEvent):
            self.queue_total = len(event.steering) + len(event.follow_up)

    @property
    def activities(self) -> list[Activity]:
        """PC activity, newest first."""

        return list(reversed(self._activities.values()))

    def _message(self, message_id: str, role: str, text: str, *, streaming: bool) -> None:
        message = self._messages_by_id.get(message_id)
        if message is None:
            message = Message(message_id, role, text, streaming)
            self.messages.append(message)
            self._messages_by_id[message_id] = message
            return
        message.text = text
        message.streaming = streaming

    def _activity(self, activity: Activity) -> None:
        # An update keeps the place of the activity it updates.
        self._activities[activity.id] = activity
        while len(self._activities) > MAX_ACTIVITIES:
            self._activities.pop(next(iter(self._activities)))
