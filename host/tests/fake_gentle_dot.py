"""A scripted Gentle Dot daemon for the typed-latency tests (models and streaming)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh"]

GROUPS: list[dict[str, Any]] = [
    {
        "provider": "openai",
        "name": "OpenAI",
        "models": [
            {"provider": "openai", "id": "gpt-5.4", "name": "GPT-5.4", "images": True, "thinkingLevels": LEVELS},
            {"provider": "openai", "id": "gpt-5.4-mini", "name": "GPT-5.4 mini", "images": True,
             "thinkingLevels": ["off", "minimal", "low"]},
            {"provider": "openai", "id": "plain", "name": "Plain", "images": False},
        ],
    }
]


@dataclass
class FakeDaemon:
    """Answers hello, models_list, model_set and send like the real bridge does."""

    provider: str = "openai"
    model_id: str = "gpt-5.4"
    thinking: str | None = "medium"
    #: Seconds before the echo, the first delta, and the done frame.
    delays: tuple[float, float, float] = (0.0, 0.05, 0.05)
    #: ``error`` code answered to every ``send`` instead of a reply (e.g. ``no_account``).
    send_error: str | None = None
    #: Never answer a ``send`` (to test the timeout).
    silent: bool = False
    received: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0

    def _choice(self, provider: str, model_id: str) -> dict[str, Any] | None:
        for group in GROUPS:
            for model in group["models"]:
                if model["provider"] == provider and model["id"] == model_id:
                    return model
        return None

    def models_payload(self) -> dict[str, Any]:
        choice = self._choice(self.provider, self.model_id) or {"name": self.model_id}
        current: dict[str, Any] = {"provider": self.provider, "id": self.model_id, "name": choice["name"]}
        if self.thinking is not None:
            current["thinking"] = self.thinking
        return {"type": "models", "current": current, "groups": GROUPS, "profiles": []}

    async def handler(self, socket: Any) -> None:
        async def emit(payload: dict[str, Any]) -> None:
            self._seq += 1
            await socket.send(json.dumps({**payload, "seq": self._seq}))

        async for raw in socket:
            message = json.loads(raw)
            self.received.append(message)
            kind = message.get("type")
            if kind == "hello":
                choice = self._choice(self.provider, self.model_id) or {"name": self.model_id}
                await emit({"type": "ready", "agentState": "idle", "model": choice["name"]})
            elif kind == "models_list":
                await emit(self.models_payload())
            elif kind == "model_set":
                choice = self._choice(message["provider"], message["id"])
                thinking = message.get("thinking")
                if choice is None:
                    await emit({"type": "error", "code": "model_unavailable",
                                "message": "That model is not available. Connect its account in Accounts first."})
                elif thinking and thinking not in choice.get("thinkingLevels", []):
                    await emit({"type": "error", "code": "thinking_unavailable",
                                "message": "That model does not support that thinking level."})
                else:
                    self.provider, self.model_id = message["provider"], message["id"]
                    if thinking:
                        self.thinking = thinking
                    await emit(self.models_payload())
            elif kind == "send":
                if self.silent:
                    continue
                if self.send_error:
                    await emit({"type": "error", "code": self.send_error, "message": "Connect an AI account first."})
                    continue
                n = sum(1 for m in self.received if m.get("type") == "send")
                echo, first, done = self.delays
                await emit({"type": "agent_state", "state": "working"})
                await asyncio.sleep(echo)
                await emit({"type": "user_message", "messageId": f"u{n}", "text": message["text"]})
                await emit({"type": "message_delta", "messageId": f"a{n}", "delta": ""})
                await asyncio.sleep(first)
                await emit({"type": "message_delta", "messageId": f"a{n}", "delta": "ok"})
                await asyncio.sleep(done)
                await emit({"type": "message_done", "messageId": f"a{n}", "text": "ok"})
                await emit({"type": "agent_state", "state": "idle"})
