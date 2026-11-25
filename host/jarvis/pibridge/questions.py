"""An extension's questions to the user, between Pi's extension UI and the clients' ``ask`` (S7).

Pi's dialog methods (``confirm``, ``select``, ``input``, ``editor``) wait for an
``extension_ui_response`` with the request's ``id``; the other methods are fire and
forget and get no answer (``docs/rpc-extension-ui.md``). A dialog becomes an ``ask`` for
the clients; a client's ``ui_response`` becomes the answer: ``confirmed`` for a confirm,
``value`` for the others, and ``cancelled`` when the answer has no usable value (the Jarvis
clients only send ``confirmed``, so they can deny but not fill a select or an input).
"""

from __future__ import annotations

from typing import Any, Final

DIALOGS: Final = frozenset({"confirm", "select", "input", "editor"})


def ask_frame(request: dict[str, Any]) -> dict[str, Any]:
    ask: dict[str, Any] = {"requestId": request["id"], "method": request["method"]}
    title = request.get("title")
    ask["title"] = title if isinstance(title, str) else ""
    message = request.get("message")
    if isinstance(message, str) and message:
        ask["message"] = message
    timeout = request.get("timeout")
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
        ask["timeoutMs"] = int(timeout)
    return {"type": "ask", "ask": ask}


def denial(request_id: str) -> dict[str, Any]:
    return {"type": "extension_ui_response", "id": request_id, "cancelled": True}


def answer(request_id: str, method: str, response: dict[str, Any]) -> dict[str, Any]:
    """Pi's ``extension_ui_response`` for a client's ``ui_response``."""

    if response.get("cancelled") is True:
        return denial(request_id)
    if method == "confirm":
        return {"type": "extension_ui_response", "id": request_id, "confirmed": response.get("confirmed") is True}
    value = response.get("value")
    if isinstance(value, str):
        return {"type": "extension_ui_response", "id": request_id, "value": value}
    return denial(request_id)


class Questions:
    """The dialogs waiting for a person, by Pi request id."""

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}

    def __bool__(self) -> bool:
        return bool(self._pending)

    def open(self, request_id: str, method: str) -> None:
        self._pending[request_id] = method

    def close(self, request_id: str) -> str | None:
        """The method of the request, or ``None`` when it is not waiting (already answered)."""

        return self._pending.pop(request_id, None)

    def drain(self) -> list[str]:
        ids = list(self._pending)
        self._pending.clear()
        return ids
