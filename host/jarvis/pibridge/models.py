"""Pi's models in the ``models`` frame the clients parse (S8).

Pi lists its models with ``get_available_models`` (full Model objects: ``provider``,
``id``, ``name``, ``reasoning``, ``input``), the running one and its thinking level with
``get_state``, and the exact levels of the running model with
``get_available_thinking_levels``. Pi does not list the levels of the other models: a
model that reasons is offered ``off`` to ``high`` (Pi's levels that every reasoning model
has); ``xhigh`` and ``max`` show only for the running model, when Pi says it has them.

A switch is whole or nothing (S13.4): ``apply`` sets the model, then the thinking level, and
puts Pi back on the previous model and level when the second step fails.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

#: Sends one Pi command and returns its ``response`` (``ModelError`` when Pi does not answer).
Command = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

#: The thinking levels of protocol v1 (``jarvis.client.THINKING_LEVELS``).
THINKING_LEVELS: Final = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
BASE_LEVELS: Final = ("off", "minimal", "low", "medium", "high")


class ModelError(RuntimeError):
    """A ``models_list`` or ``model_set`` that failed; ``code`` is the protocol's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NoAnswer(ModelError):
    """Pi did not answer a command (silent, gone or closed): send that Pi nothing more (S18.3)."""


@dataclass(frozen=True, slots=True)
class Choice:
    """A model and thinking level (``None``: leave Pi's level as it is)."""

    provider: str
    model_id: str
    thinking: str | None = None

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model_id}"


def error_of(response: dict[str, Any], fallback: str) -> str:
    error = response.get("error")
    return error if isinstance(error, str) and error else fallback


async def running(command: Command) -> Choice | None:
    """The model and thinking level Pi runs, from ``get_state``."""

    response = await command({"type": "get_state"})
    if response.get("success") is not True:
        raise ModelError("agent_unavailable", error_of(response, "Pi refused get_state."))
    data = response.get("data")
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, dict):
        return None
    provider, model_id = model.get("provider"), model.get("id")
    if not isinstance(provider, str) or not isinstance(model_id, str):
        return None
    thinking = data.get("thinkingLevel") if isinstance(data, dict) else None
    return Choice(provider, model_id, thinking if isinstance(thinking, str) else None)


async def apply(command: Command, choice: Choice, previous: Choice | None) -> None:
    """Switch Pi to ``choice`` whole, or put it back on ``previous`` and raise ``ModelError``."""

    switched = await command({"type": "set_model", "provider": choice.provider, "modelId": choice.model_id})
    if switched.get("success") is not True:
        raise ModelError("model_switch_failed", error_of(switched, "Pi could not switch the model."))
    if choice.thinking is None:
        return
    try:
        listed = await command({"type": "get_available_thinking_levels"})
        data = listed.get("data")
        levels = data.get("levels") if isinstance(data, dict) else None
        if listed.get("success") is not True or not isinstance(levels, list) or choice.thinking not in levels:
            raise ModelError("thinking_unavailable", "That model does not support that thinking level.")
        done = await command({"type": "set_thinking_level", "level": choice.thinking})
        if done.get("success") is not True:
            raise ModelError("thinking_unavailable", error_of(done, "Pi refused that thinking level."))
    except NoAnswer:
        raise
    except ModelError:
        await put_back(command, previous)
        raise


async def put_back(command: Command, previous: Choice | None) -> None:
    if previous is None:
        logger.warning("could not put pi back on its previous model: it was not known")
        return
    try:
        back = await command({"type": "set_model", "provider": previous.provider, "modelId": previous.model_id})
        if back.get("success") is not True:
            raise ModelError("model_switch_failed", error_of(back, "Pi could not switch the model."))
        if previous.thinking is not None:
            level = await command({"type": "set_thinking_level", "level": previous.thinking})
            if level.get("success") is not True:
                logger.warning("pi is back on %s but refused thinking level %s", previous.name, previous.thinking)
    except ModelError as error:
        logger.warning("could not put pi back on %s: %s", previous.name, error.message)


def _key(model: dict[str, Any]) -> tuple[object, object]:
    return model.get("provider"), model.get("id")


def find(models: list[dict[str, Any]], provider: str, model_id: str) -> dict[str, Any] | None:
    return next((m for m in models if _key(m) == (provider, model_id)), None)


def reasons(model: dict[str, Any]) -> bool:
    return model.get("reasoning") is True


def _levels(model: dict[str, Any], current: dict[str, Any] | None, current_levels: list[str]) -> list[str]:
    if current is not None and _key(model) == _key(current):
        levels = [level for level in current_levels if level in THINKING_LEVELS]
        return [] if levels in ([], ["off"]) else levels
    return list(BASE_LEVELS) if reasons(model) else []


def _ref(model: dict[str, Any], thinking: object) -> dict[str, Any]:
    ref: dict[str, Any] = {"provider": model.get("provider"), "id": model.get("id"),
                           "name": model.get("name") or model.get("id")}
    if reasons(model) and thinking in THINKING_LEVELS:
        ref["thinking"] = thinking
    return ref


def models_frame(
    models: list[dict[str, Any]],
    current: dict[str, Any] | None,
    thinking: object,
    current_levels: list[str],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for model in models:
        provider, model_id = model.get("provider"), model.get("id")
        if not isinstance(provider, str) or not isinstance(model_id, str):
            continue
        inputs = model.get("input")
        groups.setdefault(provider, []).append({
            "provider": provider,
            "id": model_id,
            "name": model.get("name") or model_id,
            "images": isinstance(inputs, list) and "image" in inputs,
            "thinkingLevels": _levels(model, current, current_levels),
        })
    frame: dict[str, Any] = {
        "type": "models",
        "groups": [{"provider": p, "name": p, "models": items} for p, items in groups.items()],
    }
    if current is not None:
        frame["current"] = _ref(current, thinking)
    return frame
