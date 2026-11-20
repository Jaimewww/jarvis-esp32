"""Time the typed path of the Gentle Dot daemon: ``python -m jarvis.bench``.

Connects like the window (same URL flags, the token read from a file by path, never
printed), prints the running model and its thinking levels, then sends a few short
prompts and prints, per run and as medians, the seconds from sending to the echo
(``user_message``), to the first non-empty ``message_delta`` and to ``message_done``.

``--model`` / ``--thinking`` send ``model_set`` first. The daemon SAVES that choice
as its default model: it outlasts this command and applies to every window.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final, TextIO

from .client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TOKEN_PATH,
    THINKING_LEVELS,
    AgentStateEvent,
    ConnectionStatus,
    ErrorEvent,
    Event,
    JarvisClient,
    MessageDeltaEvent,
    MessageDoneEvent,
    ModelRef,
    ModelsEvent,
    Ready,
    UserMessageEvent,
    parse_model_spec,
)

DEFAULT_PROMPT: Final = "Responde solo: ok"
DEFAULT_RUNS: Final = 3
DEFAULT_TIMEOUT: Final = 120.0
CONNECT_TIMEOUT: Final = 10.0
REPLY_TIMEOUT: Final = 10.0

EXIT_OK: Final = 0
#: argparse uses 2 for usage errors.
EXIT_CONNECT: Final = 3
EXIT_DAEMON_ERROR: Final = 4
EXIT_TIMEOUT: Final = 5

EPILOG = f"""\
--model and --thinking send model_set before the runs. The daemon persists that
choice as its default model (and thinking level) for every window and later
sessions; run again with the old values to go back.

exit codes: {EXIT_OK} ok; 2 usage; {EXIT_CONNECT} cannot connect (no daemon, token
missing or refused); {EXIT_DAEMON_ERROR} the daemon answered an error (printed as
code: message, e.g. thinking_unavailable, model_unavailable, no_account);
{EXIT_TIMEOUT} timed out.
"""


class BenchError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _model_spec(value: str) -> str:
    try:
        parse_model_spec(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return value


def _runs(value: str) -> int:
    try:
        n = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from error
    if n < 1:
        raise argparse.ArgumentTypeError("at least 1 run")
    return n


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.bench",
        description=(
            "Time typed prompts on the Gentle Dot daemon: seconds to the echo, to the "
            "first text and to the end of the reply, per run and as medians."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="daemon host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="daemon port (default: %(default)s)")
    parser.add_argument("--url", default=None, help="full WebSocket URL; overrides --host and --port")
    parser.add_argument("--token-file", default=None, help=f"access token file (default: {DEFAULT_TOKEN_PATH})")
    parser.add_argument("-n", "--runs", type=_runs, default=DEFAULT_RUNS, help="prompts to send (default: %(default)s)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="the prompt (default: %(default)r)")
    parser.add_argument(
        "--model", type=_model_spec, default=None, metavar="PROVIDER/ID",
        help="model_set this model first (persists as the daemon's default)",
    )
    parser.add_argument(
        "--thinking", choices=THINKING_LEVELS, default=None, metavar="LEVEL",
        help=f"model_set this thinking level first (persists as the daemon's default): {', '.join(THINKING_LEVELS)}",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list the models and their thinking levels and send no prompt (after --model/--thinking, if given)",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help="seconds to wait for each reply (default: %(default)s)",
    )
    return parser.parse_args(argv)


def _s(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds:.2f}s"


def _describe(ref: ModelRef | None) -> str:
    if ref is None:
        return "model: — (the daemon reports none)"
    return f"model: {ref.spec} ({ref.name}) · thinking: {ref.thinking or '—'}"


def _median(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return statistics.median(known) if known else None


class Session:
    """One connection; events land in a queue the steps below read in order."""

    def __init__(self, args: argparse.Namespace) -> None:
        token_path = Path(args.token_file).expanduser() if args.token_file else None
        self.events: asyncio.Queue[Event] = asyncio.Queue()
        self.status = ConnectionStatus.DISCONNECTED
        self.detail = ""
        self.agent_state = "starting"
        self.client = JarvisClient(
            self.events.put_nowait, self._on_status,
            host=args.host, port=args.port, url=args.url, token_path=token_path,
        )

    def _on_status(self, status: ConnectionStatus, detail: str) -> None:
        self.status, self.detail = status, detail

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CONNECT_TIMEOUT
        while not self.client.connected:
            # The client retries on its own; the first failure is reason enough here.
            if self.status in (ConnectionStatus.DISCONNECTED, ConnectionStatus.UNAUTHORIZED,
                               ConnectionStatus.PROTOCOL_MISMATCH) and self.detail not in ("", "not connected"):
                raise BenchError(EXIT_CONNECT, self.detail.split(" Retrying in ")[0])
            if loop.time() > deadline:
                raise BenchError(EXIT_CONNECT, f"could not connect within {CONNECT_TIMEOUT:g}s")
            await asyncio.sleep(0.02)

    async def next(self, accept: Callable[[Event], bool], timeout: float, what: str) -> Event:
        """The next event ``accept`` takes; an ``error`` frame or the deadline ends the wait."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise BenchError(EXIT_TIMEOUT, f"timed out after {timeout:g}s waiting for {what}")
            try:
                event = await asyncio.wait_for(self.events.get(), remaining)
            except TimeoutError:
                continue
            if isinstance(event, (Ready, AgentStateEvent)):
                self.agent_state = event.agent_state if isinstance(event, Ready) else event.state
            if isinstance(event, ErrorEvent):
                raise BenchError(EXIT_DAEMON_ERROR, f"{event.code}: {event.message}")
            if accept(event):
                return event


async def _models(session: Session) -> ModelsEvent:
    session.client.models_list()
    event = await session.next(lambda e: isinstance(e, ModelsEvent), REPLY_TIMEOUT, "the models list")
    assert isinstance(event, ModelsEvent)
    return event


def _print_current(models: ModelsEvent, out: TextIO) -> None:
    print(_describe(models.current), file=out)
    if models.next is not None:
        print(f"next message: {_describe(models.next)}", file=out)
    current = models.current
    choice = models.choice(current.provider, current.id) if current else None
    levels = ", ".join(choice.thinking_levels) if choice and choice.thinking_levels else "— (does not think)"
    print(f"thinking levels: {levels}", file=out)


def _print_list(models: ModelsEvent, out: TextIO) -> None:
    current = models.current.spec if models.current else None
    for group in models.groups:
        print(f"{group.name} ({group.provider})", file=out)
        for model in group.models:
            mark = "*" if model.spec == current else " "
            levels = ", ".join(model.thinking_levels) or "—"
            print(f"  {mark} {model.spec}  {model.name}  thinking: {levels}", file=out)


async def _model_set(session: Session, args: argparse.Namespace, models: ModelsEvent, out: TextIO) -> ModelsEvent:
    if args.model:
        provider, model_id = parse_model_spec(args.model)
    elif models.current is not None:
        provider, model_id = models.current.provider, models.current.id
    else:
        raise BenchError(EXIT_DAEMON_ERROR, "no running model to set a thinking level on; pass --model")
    session.client.model_set(provider, model_id, args.thinking)
    level = f" thinking {args.thinking}" if args.thinking else ""
    print(f"model_set {provider}/{model_id}{level}: saved as the daemon's default", file=out)

    def applied(event: Event) -> bool:
        if not isinstance(event, ModelsEvent):
            return False
        for ref in (event.current, event.next):
            if ref and (ref.provider, ref.id) == (provider, model_id) and (
                args.thinking is None or ref.thinking == args.thinking
            ):
                return True
        return False

    event = await session.next(applied, REPLY_TIMEOUT, "the model switch")
    assert isinstance(event, ModelsEvent)
    return event


async def _wait_idle(session: Session, timeout: float) -> None:
    if session.agent_state == "idle":
        return
    await session.next(lambda _e: session.agent_state == "idle", timeout, "the agent to be idle")


async def _one_run(session: Session, prompt: str, timeout: float) -> tuple[float | None, float | None, float]:
    sent = time.monotonic()
    if not session.client.send(prompt):
        raise BenchError(EXIT_CONNECT, "the connection dropped before sending")
    echo: float | None = None
    first: float | None = None
    try:
        while True:
            event = await session.next(
                lambda e: isinstance(e, (UserMessageEvent, MessageDeltaEvent, MessageDoneEvent)),
                max(0.0, sent + timeout - time.monotonic()),
                "the reply",
            )
            now = time.monotonic() - sent
            if isinstance(event, UserMessageEvent) and echo is None and event.text == prompt:
                echo = now
            elif echo is None:
                continue  # frames of a reply that started before this prompt
            elif isinstance(event, MessageDeltaEvent) and event.delta and first is None:
                first = now
            elif isinstance(event, MessageDoneEvent):
                if event.error:
                    raise BenchError(EXIT_DAEMON_ERROR, f"reply failed: {event.error}")
                return echo, first if first is not None else (now if event.text else None), now
    except BenchError as error:
        if error.code == EXIT_TIMEOUT:
            session.client.abort()  # do not leave the prompt running
            await asyncio.sleep(0.1)
        raise


async def run(argv: list[str] | None = None, out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    args = parse_args(argv)
    session = Session(args)
    task = asyncio.create_task(session.client.run(), name="jarvis-bench")
    try:
        await session.connect()
        models = await _models(session)
        _print_current(models, out)
        if args.model or args.thinking:
            models = await _model_set(session, args, models, out)
            _print_current(models, out)
        if args.list:  # after a model_set, so it can also just switch (and restore)
            _print_list(models, out)
            return EXIT_OK
        runs: list[tuple[float | None, float | None, float]] = []
        for i in range(args.runs):
            await _wait_idle(session, args.timeout)
            echo, first, total = await _one_run(session, args.prompt, args.timeout)
            runs.append((echo, first, total))
            print(f"run {i + 1}/{args.runs}: echo {_s(echo)} · first text {_s(first)} · total {_s(total)}", file=out)
        print(
            f"median (n={len(runs)}): echo {_s(_median([r[0] for r in runs]))} · "
            f"first text {_s(_median([r[1] for r in runs]))} · total {_s(_median([r[2] for r in runs]))}",
            file=out,
        )
        return EXIT_OK
    except BenchError as error:
        print(f"jarvis.bench: {error}", file=err)
        return error.code
    finally:
        await session.client.close()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, 5)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
