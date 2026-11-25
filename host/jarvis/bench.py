"""Time whole turns on the Gentle Dot daemon: ``python -m jarvis.bench``.

Connects like the window (same URL flags, the token read from a file by path, never
printed), prints the running model and its thinking levels, then runs the fixed request
set of ``bench_requests.json`` (S5) N times each and reports, per request, the median and
the worst case of the total time and of the model calls, timed by the window's own code
(``HostModel`` and ``TurnTrace``) from sending to the agent going back to ``idle``.
Requests that act on the desktop (catalog commands, PC tasks) run only with
``--include-pc``; ``--dry-run`` lists the set and connects to nothing. A run counts as
``done`` only when the turn ended without error, no tool ended other than ``done``, and,
for a catalog or PC request, at least one tool ran: the ``done`` column (``k/n``) and the
JSON say how many runs did their job, while every run is still timed.

``--prompt`` times one prompt instead: per run and as medians, the seconds from sending
to the echo (``user_message``), to the first non-empty text and to the end of the turn.

``--model`` / ``--thinking`` send ``model_set`` first. The daemon SAVES that choice
as its default model: it outlasts this command and applies to every window.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import sys
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO

from .client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TOKEN_PATH,
    THINKING_LEVELS,
    AgentStateEvent,
    Ask,
    AskEvent,
    ConnectionStatus,
    ErrorEvent,
    Event,
    JarvisClient,
    ModelRef,
    ModelsEvent,
    Ready,
    parse_model_spec,
)
from .model import HostModel
from .turntrace import TurnTrace

#: What ``--prompt`` without a value would send; kept for scripts of the one-prompt mode.
DEFAULT_PROMPT: Final = "Responde solo: ok"
REQUESTS_PATH: Final = Path(__file__).with_name("bench_requests.json")
DEFAULT_RUNS: Final = 3
DEFAULT_TIMEOUT: Final = 120.0
CONNECT_TIMEOUT: Final = 10.0
REPLY_TIMEOUT: Final = 10.0

EXIT_OK: Final = 0
EXIT_USAGE: Final = 2
#: argparse uses 2 for usage errors.
EXIT_CONNECT: Final = 3
EXIT_DAEMON_ERROR: Final = 4
EXIT_TIMEOUT: Final = 5

EPILOG = f"""\
Requests marked desktop (catalog commands like "Abrí Spotify", the 3 PC tasks) act on
the REAL desktop: they run only with --include-pc, and their confirmations are asked
here (y/N). Without it, any confirmation the agent asks is denied. --only takes request
ids or kinds (question, catalog, pc) and may repeat.

The done column counts the runs that did their job: no error, no tool ending failed,
and for catalog/pc requests at least one tool (an agent that only answers "I cannot"
is not done). The errors column counts protocol errors only.

--model and --thinking send model_set before the runs. The daemon persists that
choice as its default model (and thinking level) for every window and later
sessions; run again with the old values to go back.

exit codes: {EXIT_OK} ok; 2 usage; {EXIT_CONNECT} cannot connect (no daemon, token
missing or refused); {EXIT_DAEMON_ERROR} the daemon answered an error (printed as
code: message, e.g. thinking_unavailable, model_unavailable, no_account);
{EXIT_TIMEOUT} timed out.
"""


#: Answers a ``confirm`` ask for the person at the terminal: ``True`` allows it.
AskHuman = Callable[[Ask], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    kind: str
    text: str
    #: Acts on the real desktop: runs only with ``--include-pc``.
    desktop: bool


def load_requests(path: Path = REQUESTS_PATH) -> list[Request]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Request(str(r["id"]), str(r["kind"]), str(r["text"]), r.get("desktop", True) is not False)
        for r in data["requests"]
    ]


def select(requests: Sequence[Request], *, only: Iterable[str] = (), include_pc: bool = False) -> list[Request]:
    """The requests to run: all of them or those ``only`` names (ids or kinds), never a
    desktop one without ``include_pc`` (named explicitly, that is an error)."""

    only = list(only)
    if not only:
        return [r for r in requests if include_pc or not r.desktop]
    known = {r.id for r in requests} | {r.kind for r in requests}
    unknown = [name for name in only if name not in known]
    if unknown:
        raise ValueError(f"unknown request or kind: {', '.join(unknown)}")
    chosen = [r for r in requests if r.id in only or r.kind in only]
    chosen.sort(key=lambda r: (r.id not in only, only.index(r.id) if r.id in only else 0))
    blocked = [r.id for r in chosen if r.desktop and not include_pc]
    if blocked:
        raise ValueError(f"{', '.join(blocked)} act on the desktop: pass --include-pc to run them")
    return chosen


#: Request kinds whose job is an action on the desktop: a run that used no tool did not do it.
DESKTOP_TASK_KINDS: Final = frozenset({"catalog", "pc"})


def run_problem(trace: TurnTrace, *, desktop_task: bool) -> str | None:
    """Why this run did not do its job, or ``None`` when it did: the turn failed
    (``error``), a tool did not end ``done`` (``tool failed``), or a desktop task used no
    tool at all (``no tool``, e.g. the agent only answered that it cannot)."""

    if trace.error is not None or trace.ended_by != "idle":
        return "error"
    if any(tool.status != "done" for tool in trace.tools):
        return "tool failed"
    if desktop_task and not trace.tools:
        return "no tool"
    return None


def _stat(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "worst": None}
    return {"median": statistics.median(values), "worst": max(values)}


def _rounded(stat: dict[str, float | None]) -> dict[str, float | None]:
    return {k: None if v is None else round(v, 3) for k, v in stat.items()}


def summarize(traces: Sequence[TurnTrace], *, desktop_task: bool = False) -> dict[str, Any]:
    """Median and worst case of one request's runs: total, active (total minus a
    person's answers), model calls and the wait for a person; failed runs counted.
    ``done`` counts the runs that did their job (``run_problem``); ``not_done`` counts
    the others by reason. Every run is timed, done or not."""

    timed = [t for t in traces if t.total_s is not None]
    problems = [run_problem(t, desktop_task=desktop_task) for t in traces]
    not_done: dict[str, int] = {}
    for problem in problems:
        if problem is not None:
            not_done[problem] = not_done.get(problem, 0) + 1
    return {
        "runs": len(traces),
        "done": problems.count(None),
        "not_done": not_done,
        "errors": sum(1 for t in traces if t.error is not None or t.ended_by not in ("idle",)),
        "total_s": _rounded(_stat([t.total_s for t in timed if t.total_s is not None])),
        "active_s": _rounded(_stat([t.total_s - t.human_wait_s for t in timed if t.total_s is not None])),
        "model_calls": _stat([len(t.calls) for t in traces]),
        "human_wait_s": _rounded(_stat([t.human_wait_s for t in traces])),
    }


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
            "Time whole turns on the Gentle Dot daemon: the fixed request set (median and "
            "worst case of total time and model calls per request), or one --prompt."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="daemon host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="daemon port (default: %(default)s)")
    parser.add_argument("--url", default=None, help="full WebSocket URL; overrides --host and --port")
    parser.add_argument("--token-file", default=None, help=f"access token file (default: {DEFAULT_TOKEN_PATH})")
    parser.add_argument("-n", "--runs", type=_runs, default=DEFAULT_RUNS, help="runs per request (default: %(default)s)")
    parser.add_argument("--prompt", default=None, help="time this one prompt instead of the set")
    parser.add_argument("--set", dest="set_path", type=Path, default=REQUESTS_PATH, metavar="FILE",
                        help="request set (default: bench_requests.json next to this module)")
    parser.add_argument("--only", action="append", default=[], metavar="ID|KIND",
                        help="run only these request ids or kinds (question, catalog, pc); repeatable")
    parser.add_argument("--include-pc", action="store_true",
                        help="also run the requests that act on the REAL desktop (catalog, PC tasks)")
    parser.add_argument("--dry-run", action="store_true", help="list the requests and send nothing")
    parser.add_argument("--json", dest="json_path", type=Path, default=None, metavar="FILE",
                        help="write every run and the statistics as JSON to FILE")
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


def _median(values: Sequence[float | None]) -> float | None:
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
        #: The window's own timing: every event goes through it, each ended turn lands here.
        self.finished: list[TurnTrace] = []
        self.host = HostModel(on_turn=self.finished.append)
        self.client = JarvisClient(
            self.events.put_nowait, self._on_status,
            host=args.host, port=args.port, url=args.url, token_path=token_path,
        )

    def _on_status(self, status: ConnectionStatus, detail: str) -> None:
        self.status, self.detail = status, detail
        self.host.set_daemon_status(status, detail)

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
            self.host.apply_daemon(event)
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


async def _turn(session: Session, text: str, timeout: float, on_ask: Callable[[Ask], Awaitable[None]]) -> TurnTrace:
    """Send ``text`` and wait for the whole turn (``idle``, an error, a stop) to end."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    session.finished.clear()
    session.host.note_sent()
    if not session.client.send(text):
        raise BenchError(EXIT_CONNECT, "the connection dropped before sending")
    try:
        while not session.finished:
            event = await session.next(
                lambda e: bool(session.finished) or isinstance(e, AskEvent),
                max(0.0, deadline - loop.time()), "the reply",
            )
            if isinstance(event, AskEvent) and not session.finished:
                await on_ask(event.ask)
        return session.finished.pop(0)
    except BenchError as error:
        if error.code == EXIT_TIMEOUT:
            session.client.abort()  # do not leave the prompt running
            await asyncio.sleep(0.1)
        raise


async def _terminal_ask(ask: Ask) -> bool:
    """Ask the person at this terminal; anything but y/yes (or no terminal) denies."""

    if not sys.stdin.isatty():
        return False
    try:
        answer = await asyncio.to_thread(input, f"  confirm: {ask.title} {ask.message or ''} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes", "s", "si", "sí")


def _asker(session: Session, include_pc: bool, ask_human: AskHuman, out: TextIO) -> Callable[[Ask], Awaitable[None]]:
    async def on_ask(ask: Ask) -> None:
        if ask.method != "confirm":
            print(f"  the agent asks {ask.method!r} ({ask.title}): answer it in Gentle Dot", file=out)
            return
        if not include_pc:
            session.client.ui_response(ask.request_id, confirmed=False)
            print(f"  confirmation denied (no --include-pc): {ask.title}", file=out)
            return
        key = f"daemon:{ask.request_id}"
        session.host.note_confirms({key})  # a person's time, reported apart from the agent's
        allowed = await ask_human(ask)
        session.client.ui_response(ask.request_id, confirmed=allowed)
        session.host.note_confirms(())
        print(f"  confirmation {'allowed' if allowed else 'denied'}: {ask.title}", file=out)

    return on_ask


def _print_dry_run(requests: Sequence[Request], args: argparse.Namespace, out: TextIO) -> None:
    chosen = select(requests, only=args.only, include_pc=args.include_pc) if args.only else requests
    for r in chosen:
        skipped = "  (skipped: acts on the desktop, needs --include-pc)" if r.desktop and not args.include_pc else ""
        print(f"{r.id:<14} {r.kind:<9} {r.text}{skipped}", file=out)
    runnable = [r for r in chosen if args.include_pc or not r.desktop]
    print(f"{len(runnable)} of {len(chosen)} requests x {args.runs} runs; nothing sent (--dry-run)", file=out)


def _fmt_stat(stat: dict[str, Any], unit: str = "s") -> str:
    if stat["median"] is None:
        return "—"
    return f"{stat['median']:.2f}{unit} / {stat['worst']:.2f}{unit}" if unit else f"{stat['median']:g} / {stat['worst']:g}"


async def _run_set(session: Session, args: argparse.Namespace, chosen: Sequence[Request],
                   models: ModelsEvent, ask_human: AskHuman, out: TextIO) -> None:
    on_ask = _asker(session, args.include_pc, ask_human, out)
    results: list[dict[str, Any]] = []
    for request in chosen:
        desktop_task = request.kind in DESKTOP_TASK_KINDS
        traces: list[TurnTrace] = []
        details: list[dict[str, Any]] = []
        for i in range(args.runs):
            await _wait_idle(session, args.timeout)
            trace = await _turn(session, request.text, args.timeout, on_ask)
            traces.append(trace)
            problem = run_problem(trace, desktop_task=desktop_task)
            details.append({**trace.record(), "done": problem is None, "not_done": problem})
            failed = f" · {trace.ended_by}: {trace.error}" if trace.error or trace.ended_by != "idle" else ""
            not_done = f" · not done: {problem}" if problem is not None else ""
            print(f"[{request.id}] {i + 1}/{args.runs}: total {_s(trace.total_s)} · first text "
                  f"{_s(trace.first_text_s)} · {trace.summary()}{failed}{not_done}", file=out)
        results.append({"id": request.id, "kind": request.kind, "text": request.text,
                        "desktop": request.desktop, **summarize(traces, desktop_task=desktop_task),
                        "runs_detail": details})
    print(f"\n{'request':<14} {'kind':<9} {'total median / worst':<22} {'calls median / worst':<21} "
          f"{'wait median':<12} {'done':<6} errors", file=out)
    for r in results:
        wait = "—" if r["human_wait_s"]["median"] is None else f"{r['human_wait_s']['median']:.2f}s"
        done = f"{r['done']}/{r['runs']}"
        print(f"{r['id']:<14} {r['kind']:<9} {_fmt_stat(r['total_s']):<22} {_fmt_stat(r['model_calls'], ''):<21} "
              f"{wait:<12} {done:<6} {r['errors']}", file=out)
    if args.json_path is not None:
        current = models.current
        document = {
            "v": 1, "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": current.spec if current else None, "thinking": current.thinking if current else None,
            "runs": args.runs, "include_pc": args.include_pc,
            "requests": [{**{k: v for k, v in r.items() if k != "runs_detail"}, "runs": r["runs_detail"]}
                         for r in results],
        }
        args.json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {args.json_path}", file=out)


async def _run_prompt(session: Session, args: argparse.Namespace, out: TextIO) -> None:
    async def no_ask(ask: Ask) -> None:
        print(f"  the agent asks: {ask.title} (answer it in Gentle Dot or the window)", file=out)

    runs: list[TurnTrace] = []
    for i in range(args.runs):
        await _wait_idle(session, args.timeout)
        trace = await _turn(session, args.prompt, args.timeout, no_ask)
        if trace.error:
            raise BenchError(EXIT_DAEMON_ERROR, f"reply failed: {trace.error}")
        runs.append(trace)
        echo = None if trace.echo_at is None else trace.echo_at - trace.sent_at
        print(f"run {i + 1}/{args.runs}: echo {_s(echo)} · first text {_s(trace.first_text_s)} · "
              f"total {_s(trace.total_s)}", file=out)
    echoes = [None if t.echo_at is None else t.echo_at - t.sent_at for t in runs]
    print(
        f"median (n={len(runs)}): echo {_s(_median(echoes))} · "
        f"first text {_s(_median([t.first_text_s for t in runs]))} · total {_s(_median([t.total_s for t in runs]))}",
        file=out,
    )


async def run(argv: list[str] | None = None, out: TextIO = sys.stdout, err: TextIO = sys.stderr,
              *, ask_human: AskHuman = _terminal_ask) -> int:
    args = parse_args(argv)
    chosen: list[Request] = []
    if args.dry_run and args.prompt is not None:
        print(f"--prompt {args.prompt!r} x {args.runs} runs; nothing sent (--dry-run)", file=out)
        return EXIT_OK
    if args.prompt is None and (args.dry_run or not args.list):
        try:
            requests = load_requests(args.set_path)
            if args.dry_run:  # before connecting: never calls the daemon
                _print_dry_run(requests, args, out)
                return EXIT_OK
            chosen = select(requests, only=args.only, include_pc=args.include_pc)
        except (OSError, ValueError, KeyError) as error:
            print(f"jarvis.bench: {error}", file=err)
            return EXIT_USAGE
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
        if args.prompt is not None:
            await _run_prompt(session, args, out)
        else:
            await _run_set(session, args, chosen, models, ask_human, out)
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
