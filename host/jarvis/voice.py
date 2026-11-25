"""Push-to-talk for the window: record the microphone, transcribe it with whisper-server.

The window (``JarvisApp``, key f4) records the default microphone as WAV PCM 16 kHz
mono 16-bit (D6) with ``pw-record`` (or ``parecord`` / ``arecord``), posts the file to
the local ``whisper-server`` and sends the text to Gentle Dot like a typed prompt. This
module holds the two pieces without any UI toolkit: ``ProcessRecorder`` and
``WhisperTranscriber``. It is the window's own client, apart from the voice server's speech
stage; it uses the standard library only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol

#: Where the speech server listens (whisper.cpp ``whisper-server``'s ``/inference``).
STT_URL_ENV: Final = "JARVIS_STT_URL"
DEFAULT_STT_URL: Final = "http://127.0.0.1:8080/inference"
#: Seconds the transcription may take before it is given up.
STT_TIMEOUT_S: Final = 30.0
STT_LANGUAGE: Final = "es"
#: A recording stops by itself after this long.
MAX_RECORDING_S: Final = 60.0
#: Seconds a recorder gets to close its file after SIGINT before it is killed.
STOP_GRACE_S: Final = 3.0
#: A WAV header alone; a file this size or smaller holds no audio.
WAV_HEADER_BYTES: Final = 44

NO_RECORDER: Final = "No recorder found: install pw-record (PipeWire), parecord or arecord."

Which = Callable[[str], "str | None"]


class VoiceError(Exception):
    """Recording or transcription failed; the message says why, for a person."""


class Recorder(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> Path: ...
    async def cancel(self) -> None: ...


class Transcriber(Protocol):
    def transcribe(self, path: Path) -> str: ...


def recorder_command(path: Path, which: Which = shutil.which) -> list[str] | None:
    """The command that records 16 kHz mono s16 WAV to ``path``, or ``None``."""

    out = str(path)
    options = (
        ("pw-record", ["--rate", "16000", "--channels", "1", "--format", "s16", out]),
        ("parecord", ["--rate=16000", "--channels=1", "--format=s16le", "--file-format=wav", out]),
        ("arecord", ["-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", out]),
    )
    for name, args in options:
        found = which(name)
        if found:
            return [found, *args]
    return None


class ProcessRecorder:
    """One recording by an external recorder: start it, then stop (SIGINT) or cancel it."""

    def __init__(self, path: Path, *, which: Which = shutil.which) -> None:
        self.path = path
        self._which = which
        self._process: asyncio.subprocess.Process | None = None

    @property
    def returncode(self) -> int | None:
        return None if self._process is None else self._process.returncode

    async def start(self) -> None:
        command = recorder_command(self.path, self._which)
        if command is None:
            raise VoiceError(NO_RECORDER)
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise VoiceError(f"The recorder did not start: {error}") from error

    async def _end(self, sig: int) -> str:
        """Signal the recorder, wait for it (killing it after the grace) and return its stderr."""

        process = self._process
        if process is None:
            return ""
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(sig)
        try:
            _, err = await asyncio.wait_for(process.communicate(), STOP_GRACE_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            _, err = await process.communicate()
        return (err or b"").decode("utf-8", "replace").strip()

    async def stop(self) -> Path:
        died = self._process is not None and self._process.returncode is not None
        err = await self._end(signal.SIGINT)
        code = self.returncode
        # pw-record and parecord end 0 on SIGINT; arecord ends 1 after writing the file.
        empty = not self.path.exists() or self.path.stat().st_size <= WAV_HEADER_BYTES
        if empty or (died and code not in (0, None)):
            cause = err.splitlines()[-1] if err else f"it ended with code {code}"
            raise VoiceError(f"The recording failed: {cause}")
        return self.path

    async def cancel(self) -> None:
        await self._end(signal.SIGTERM)


def _multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = f"jarvis-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
            f'filename="{path.name}"\r\nContent-Type: audio/wav\r\n\r\n'
        ).encode()
        + path.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class WhisperTranscriber:
    """The text of a WAV, from whisper.cpp's ``whisper-server`` (``POST /inference``)."""

    def __init__(
        self,
        url: str | None = None,
        *,
        timeout: float = STT_TIMEOUT_S,
        language: str = STT_LANGUAGE,
    ) -> None:
        self.url = url or os.environ.get(STT_URL_ENV) or DEFAULT_STT_URL
        self.timeout = timeout
        self.language = language

    def transcribe(self, path: Path) -> str:
        """Blocking: run it off the event loop. Raises ``VoiceError`` with the cause."""

        body, content_type = _multipart(
            {"response_format": "json", "language": self.language, "temperature": "0.0"}, "file", path
        )
        request = urllib.request.Request(
            self.url, data=body, method="POST", headers={"Content-Type": content_type}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - local URL
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace").strip()[:200]
            raise VoiceError(f"The speech server answered HTTP {error.code}: {detail or error.reason}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise VoiceError(f"Can't reach the speech server at {self.url}: {reason}") from error
        try:
            text = json.loads(raw)["text"]
        except (ValueError, KeyError, TypeError) as error:
            raise VoiceError("The speech server answered no text.") from error
        if not isinstance(text, str):
            raise VoiceError("The speech server answered no text.")
        return text.strip()
