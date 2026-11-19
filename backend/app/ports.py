"""Stage interfaces of the voice turn (contract C3, docs/contratos.md).

Each stage of ``/converse`` sits behind one of these protocols (D19) so models can
be compared and tests can run with doubles (``app.fakes``). Two tracks implement
``Agent``: the voice track owns the chat engine; the host track owns the Gentle
Dot adapter for long tasks (D14).

Audio crossing these interfaces is always WAV PCM 16 kHz, mono, 16-bit (D6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class Transcriber(Protocol):
    """Speech to text."""

    async def transcribe(self, wav: bytes) -> str:
        """Return the transcript of a WAV PCM 16 kHz mono 16-bit clip."""
        ...


class Agent(Protocol):
    """Text in, reply text out, streamed in chunks."""

    def reply(self, text: str, *, turn_id: str) -> AsyncIterator[str]:
        """Yield the reply to ``text`` as text chunks (e.g. one per sentence)."""
        ...

    async def abort(self) -> None:
        """Stop the reply in progress, if any."""
        ...

    async def reset(self) -> None:
        """Forget the conversation context."""
        ...


class Synthesizer(Protocol):
    """Text to speech."""

    async def synthesize(self, text: str) -> bytes:
        """Return ``text`` spoken as WAV PCM 16 kHz mono 16-bit."""
        ...
