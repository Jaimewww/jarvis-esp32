"""PC control (S13): a client of the ``jarvis-input`` helper's control socket.

The helper is the only process that may type, press keys or take screenshots, and it
holds the permission in memory (restarting it revokes). This window only asks it, over
``$XDG_RUNTIME_DIR/jarvis-control/control.sock``, JSON by lines:

* ``{"op":"grant","minutes":30}`` (at most 30) → ``{"ok":true,"expires_at":"…Z"}``;
* ``{"op":"revoke"}``; ``{"op":"status"}``; each on its own short connection;
* ``{"op":"subscribe"}`` on one long-lived connection (``ControlLink``), which then gets
  ``{"event":"state","enabled":…,"expires_at":…}`` and
  ``{"event":"confirm","id":…,"title":…,"message":…,"timeout_s":30}``, and carries this
  window's ``{"op":"answer","id":…,"allow":true|false}``. No subscriber, or no answer, is
  a denial, so a dropped link denies whatever was waiting.

Nothing here writes a file: the helper is the source of truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

#: How long a grant lasts, and the most the helper accepts.
CONTROL_MINUTES: Final = 30
#: How long a confirmation waits when the asker gives no timeout (the helper's default).
CONFIRM_TIMEOUT_S: Final = 30.0
#: A request that gets no answer in this long counts as the helper not running.
REQUEST_TIMEOUT_S: Final = 3.0
USAGE: Final = "/control on · /control off · /control status"
#: Retry delays of the subscribe link, seconds: doubled from the first up to the second.
BACKOFF: Final = (0.5, 5.0)
_LIMIT: Final = 64 * 1024


class ControlError(RuntimeError):
    """PC control cannot be asked for or was refused; the message is for the person."""


class ControlUnavailable(ControlError):
    """The helper is not there to ask: ``jarvis-input is not running: …``."""


@dataclass(frozen=True, slots=True)
class ControlState:
    """The permission as the helper reports it."""

    enabled: bool
    expires_at: datetime | None

    def remaining_s(self, now: datetime) -> float:
        if not self.enabled or self.expires_at is None:
            return 0.0
        return max(0.0, (self.expires_at - now).total_seconds())


OFF: Final = ControlState(False, None)


@dataclass(frozen=True, slots=True)
class ConfirmRequest:
    """A helper ``confirm`` event: allow or deny one risky action."""

    #: Echoed as is in the ``answer``.
    id: str | int
    title: str
    message: str
    timeout_s: float


def control_socket_path(environ: Mapping[str, str] = os.environ) -> Path:
    runtime = environ.get("XDG_RUNTIME_DIR", "")
    if not runtime or not os.path.isabs(runtime):
        raise ControlError(
            "XDG_RUNTIME_DIR is not set: PC control needs your session's private runtime "
            "directory. Start Jarvis from your desktop session."
        )
    return Path(runtime) / "jarvis-control" / "control.sock"


def _unavailable(path: Path, error: BaseException) -> ControlUnavailable:
    if isinstance(error, FileNotFoundError):
        why = f"no control socket at {path}"
    elif isinstance(error, ConnectionRefusedError):
        why = f"nothing listens on {path}"
    elif isinstance(error, TimeoutError | asyncio.TimeoutError):
        why = f"no answer on {path}"
    elif isinstance(error, OSError):
        why = f"{error.strerror or error} ({path})"
    else:
        why = str(error) or type(error).__name__
    return ControlUnavailable(
        f"jarvis-input is not running: {why}. It is installed by host/systemd/install.sh; "
        "start it with: systemctl --user start jarvis-input"
    )


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def parse_state(data: Mapping[str, Any]) -> ControlState:
    """``enabled`` true with a readable expiry is on; anything else is off."""

    expires = _moment(data.get("expires_at"))
    if data.get("enabled") is not True or expires is None:
        return OFF
    return ControlState(True, expires)


async def request(message: Mapping[str, Any], path: Path | None = None,
                  timeout_s: float = REQUEST_TIMEOUT_S) -> dict[str, Any]:
    """Send one request on its own connection and return the helper's ``ok`` answer."""

    path = path or control_socket_path()
    try:
        async with asyncio.timeout(timeout_s):
            reader, writer = await asyncio.open_unix_connection(str(path), limit=_LIMIT)
            try:
                writer.write((json.dumps(dict(message)) + "\n").encode())
                await writer.drain()
                line = await reader.readline()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
    except (OSError, TimeoutError) as error:
        raise _unavailable(path, error) from error
    try:
        answer = json.loads(line)
    except ValueError as error:
        raise _unavailable(path, ConnectionError("it closed without an answer")) from error
    if not isinstance(answer, dict):
        raise ControlError("jarvis-input gave an unreadable answer: control stays as it was.")
    if answer.get("ok") is not True:
        raise ControlError(f"jarvis-input refused: {answer.get('error') or 'no reason given'}.")
    return answer


async def grant(minutes: int = CONTROL_MINUTES, path: Path | None = None) -> ControlState:
    if not 1 <= minutes <= CONTROL_MINUTES:
        raise ValueError(f"a grant lasts 1 to {CONTROL_MINUTES} minutes, not {minutes}")
    answer = await request({"op": "grant", "minutes": minutes}, path)
    state = parse_state({"enabled": True, **answer})
    if not state.enabled:
        raise ControlError("jarvis-input granted control without a readable expiry: treat it as off.")
    return state


async def revoke(path: Path | None = None) -> None:
    await request({"op": "revoke"}, path)


async def status(path: Path | None = None) -> ControlState:
    return parse_state(await request({"op": "status"}, path))


class ControlLink:
    """The ``subscribe`` connection: events in, answers out, reconnecting with backoff.

    ``on_connection(up, detail)`` is called on each change (and once for the first
    failure), not on every retry.
    """

    def __init__(
        self,
        on_state: Callable[[ControlState], None],
        on_confirm: Callable[[ConfirmRequest], None],
        on_connection: Callable[[bool, str], None],
        path: Callable[[], Path] = control_socket_path,
        backoff: tuple[float, float] = BACKOFF,
    ) -> None:
        self._on_state = on_state
        self._on_confirm = on_confirm
        self._on_connection = on_connection
        self._path = path
        self._backoff = backoff
        self._writer: asyncio.StreamWriter | None = None
        self._closed = asyncio.Event()
        self._reported: bool | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None

    def answer(self, id: str | int, allow: bool) -> bool:
        """Send an answer on the subscribe connection; ``False`` if it is down."""

        writer = self._writer
        if writer is None or writer.is_closing():
            return False
        try:
            writer.write((json.dumps({"op": "answer", "id": id, "allow": allow is True}) + "\n").encode())
        except Exception:  # noqa: BLE001 - a dropped link denies anyway
            return False
        return True

    def _report(self, up: bool, detail: str) -> None:
        if self._reported is up:
            return
        self._reported = up
        self._on_connection(up, detail)

    async def run(self) -> None:
        delay = self._backoff[0]
        while not self._closed.is_set():
            try:
                path = self._path()
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(str(path), limit=_LIMIT), REQUEST_TIMEOUT_S)
            except ControlError as error:
                self._report(False, str(error))
            except (OSError, TimeoutError) as error:
                self._report(False, str(_unavailable(path, error)))
            else:
                delay = self._backoff[0]
                await self._serve(path, reader, writer)
            if self._closed.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._closed.wait(), delay)
            delay = min(delay * 2, self._backoff[1])

    async def _serve(self, path: Path, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b'{"op":"subscribe"}\n')
            await writer.drain()
            self._writer = writer
            self._report(True, "")
            # The state at connect time; later changes come as events.
            with contextlib.suppress(ControlError):
                self._on_state(await status(path))
            closed = asyncio.create_task(self._closed.wait())
            try:
                while True:
                    line_task = asyncio.create_task(reader.readline())
                    done, _ = await asyncio.wait({line_task, closed}, return_when=asyncio.FIRST_COMPLETED)
                    if closed in done:
                        line_task.cancel()
                        with contextlib.suppress(BaseException):
                            await line_task
                        return
                    line = line_task.result()
                    if not line:
                        break
                    self._dispatch(line)
            finally:
                closed.cancel()
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            logger.debug("the control link dropped", exc_info=True)
        finally:
            self._writer = None
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if not self._closed.is_set():
                self._report(False, str(_unavailable(path, ConnectionError("the helper closed the link"))))

    def _dispatch(self, line: bytes) -> None:
        try:
            data = json.loads(line)
        except ValueError:
            logger.debug("unreadable control event dropped")
            return
        if not isinstance(data, dict):
            return
        event = data.get("event")
        if event == "state":
            self._on_state(parse_state(data))
        elif event == "confirm":
            id = data.get("id")
            if not isinstance(id, str | int) or isinstance(id, bool) or id == "":
                return
            timeout = data.get("timeout_s")
            timeout_s = float(timeout) if isinstance(timeout, int | float) and not isinstance(timeout, bool) \
                and timeout > 0 else CONFIRM_TIMEOUT_S
            title = data.get("title")
            message = data.get("message")
            self._on_confirm(ConfirmRequest(
                id,
                title if isinstance(title, str) else "",
                message if isinstance(message, str) else "",
                timeout_s,
            ))
        elif data.get("ok") is False:
            logger.warning("jarvis-input refused on the control link: %s", data.get("error"))

    async def close(self) -> None:
        self._closed.set()
        writer = self._writer
        if writer is not None:
            writer.close()


def remaining_label(seconds: float) -> str:
    """``29:12``: minutes and seconds left, rounded down."""

    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def parse_control(text: str) -> str | None:
    """``on``, ``off``, ``status`` (also bare ``/control``), ``usage``; ``None`` if not ``/control``."""

    words = text.strip().lower().split()
    if not words or words[0] != "/control":
        return None
    if len(words) == 1:
        return "status"
    if len(words) == 2 and words[1] in ("on", "off", "status"):
        return words[1]
    return "usage"
