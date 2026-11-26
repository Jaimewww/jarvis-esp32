"""The Pi child process behind the bridge (S5).

``PiProcess`` is the small interface the turn logic uses, so tests inject a fake;
``SubprocessPi`` is the real one: ``pi --mode rpc`` with JSON records on stdin and
stdout, one per LF-terminated line (``docs/rpc.md``). The command line is the one
measured on 2026-11-25 (L4 in ``odd/tasks/pi-bridge.md``): no session, no discovered
extensions, skills, prompt templates, context files or themes; the Jarvis extension
and agent rules are loaded explicitly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

logger = logging.getLogger(__name__)

#: Tool results (screenshots) can be large; one record may be this big.
MAX_RECORD_BYTES: Final = 64 * 1024 * 1024
CLOSE_GRACE_S: Final = 5.0


class PiProcess(Protocol):
    @property
    def exit_code(self) -> int | None:
        """``None`` while Pi runs."""
        ...

    async def write(self, record: Mapping[str, Any]) -> None:
        """Send one command; raises ``OSError`` once Pi is gone."""
        ...

    async def read(self) -> dict[str, Any] | None:
        """The next record from Pi, or ``None`` when its output ended (Pi exited)."""
        ...

    async def close(self) -> None: ...


PiLauncher = Callable[[], Awaitable[PiProcess]]


@dataclass(frozen=True, slots=True)
class PiSettings:
    executable: str
    model: str
    thinking: str
    pc_control: Path
    extensions: tuple[Path, ...]
    agents_md: Path
    system_prompt: str


def pi_command(settings: PiSettings) -> list[str]:
    argv = [
        settings.executable,
        "--mode", "rpc",
        "--no-session",
        "-ne", "-ns", "-np", "-nc",
        "--no-themes",
        "--model", f"{settings.model}:{settings.thinking}",
    ]
    for extension in (settings.pc_control, *settings.extensions):
        argv += ["-e", str(extension)]
    argv += ["--system-prompt", settings.system_prompt]
    argv += ["--append-system-prompt", str(settings.agents_md)]
    return argv


class SubprocessPi:
    """``pi --mode rpc`` as an asyncio child process; its stderr goes to ours."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @classmethod
    async def spawn(cls, argv: list[str]) -> SubprocessPi:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=MAX_RECORD_BYTES,
        )
        logger.info("started pi (pid %d)", process.pid)
        return cls(process)

    @property
    def exit_code(self) -> int | None:
        return self._process.returncode

    async def write(self, record: Mapping[str, Any]) -> None:
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing() or self._process.returncode is not None:
            raise BrokenPipeError("pi is not running")
        stdin.write((json.dumps(record) + "\n").encode("utf-8"))
        await stdin.drain()

    async def read(self) -> dict[str, Any] | None:
        stdout = self._process.stdout
        if stdout is None:
            raise RuntimeError("pi was started without a stdout pipe")
        while True:
            try:
                line = await stdout.readuntil(b"\n")
            except asyncio.IncompleteReadError:
                await self._process.wait()
                return None
            except asyncio.LimitOverrunError as error:
                await stdout.readexactly(error.consumed)  # drop it; its tail fails to parse
                logger.warning("skipped a pi record larger than %d bytes", MAX_RECORD_BYTES)
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # stdout is for records only; anything else is not ours to parse
            if isinstance(record, dict):
                return record

    async def close(self) -> None:
        process = self._process
        if process.returncode is not None:
            return
        if process.stdin is not None:
            process.stdin.close()  # Pi's orderly shutdown
        try:
            await asyncio.wait_for(process.wait(), CLOSE_GRACE_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), CLOSE_GRACE_S)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()


def subprocess_launcher(settings: PiSettings) -> PiLauncher:
    async def launch() -> PiProcess:
        return await SubprocessPi.spawn(pi_command(settings))

    return launch
