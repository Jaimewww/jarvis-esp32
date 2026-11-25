"""Pi's models in the ``models`` frame the clients parse (S8).

Pi lists its models with ``get_available_models`` (full Model objects: ``provider``,
``id``, ``name``, ``reasoning``, ``input``), the running one and its thinking level with
``get_state``, and the exact levels of the running model with
``get_available_thinking_levels``. Pi does not list the levels of the other models: a
model that reasons is offered ``off`` to ``high`` (Pi's levels that every reasoning model
has); ``xhigh`` and ``max`` show only for the running model, when Pi says it has them.
"""

from __future__ import annotations

from typing import Any, Final

#: The thinking levels of protocol v1 (``jarvis.client.THINKING_LEVELS``).
THINKING_LEVELS: Final = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
BASE_LEVELS: Final = ("off", "minimal", "low", "medium", "high")


class ModelError(RuntimeError):
    """A ``models_list`` or ``model_set`` that failed; ``code`` is the protocol's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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
