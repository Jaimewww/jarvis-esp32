"""The one conversation the bridge keeps in memory, for ``get_history`` and ``list_conversations`` (S9).

It is built from the frames the bridge sends (``user_message``, ``message_delta``,
``message_done``, ``activity``), so a client that connects later, or that saw a ``seq``
gap, gets the same messages the others saw, in the ``history`` shape the clients parse.
Pi runs with ``--no-session``: the conversation lives as long as the bridge does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

#: Messages kept; older ones are dropped (the clients never page back on this bridge).
MAX_MESSAGES: Final = 500
TITLE_CHARS: Final = 60
DEFAULT_TITLE: Final = "Jarvis"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class _Message:
    id: str
    role: str
    text: str = ""
    activities: dict[str, dict[str, Any]] = field(default_factory=dict)

    def frame(self) -> dict[str, Any]:
        return {"id": self.id, "role": self.role, "text": self.text, "activities": list(self.activities.values())}


class Conversation:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self._messages: dict[str, _Message] = {}
        self._title: str | None = None
        self._updated_at = _now()

    def observe(self, frame: dict[str, Any]) -> None:
        """Keep what a frame the clients receive adds to the conversation."""

        kind = frame.get("type")
        message_id = frame.get("messageId")
        if not isinstance(message_id, str) or kind not in ("user_message", "message_delta", "message_done", "activity"):
            return
        if kind == "user_message":
            text = frame.get("text")
            message = self._get(message_id, "user")
            message.text = text if isinstance(text, str) else ""
            if self._title is None and message.text.strip():
                self._title = message.text.strip()[:TITLE_CHARS]
        elif kind == "message_delta":
            delta = frame.get("delta")
            if isinstance(delta, str):
                self._get(message_id, "assistant").text += delta
        elif kind == "message_done":
            text = frame.get("text")
            message = self._get(message_id, "assistant")
            if isinstance(text, str) and text:
                message.text = text
        else:
            activity = frame.get("activity")
            if isinstance(activity, dict) and isinstance(activity.get("id"), str):
                self._get(message_id, "assistant").activities[activity["id"]] = dict(activity)
        self._updated_at = _now()

    def _get(self, message_id: str, role: str) -> _Message:
        message = self._messages.get(message_id)
        if message is None:
            message = self._messages[message_id] = _Message(message_id, role)
            while len(self._messages) > MAX_MESSAGES:
                del self._messages[next(iter(self._messages))]
        return message

    def history_frame(self) -> dict[str, Any]:
        return {
            "type": "history",
            "conversationId": self.id,
            "messages": [message.frame() for message in self._messages.values()],
            "hasEarlier": False,
        }

    def conversations_frame(self) -> dict[str, Any]:
        conversation = {"id": self.id, "title": self._title or DEFAULT_TITLE, "updatedAt": self._updated_at}
        return {"type": "conversations", "conversations": [conversation], "activeId": self.id}
