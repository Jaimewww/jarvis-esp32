"""Transcribe WAV files with whisper-server and print the text and the time.

    cd backend
    .venv/bin/python -m app.stt clip.wav [more.wav ...] [--runs 3]

Uses ``WhisperServerTranscriber.from_env()`` (``JARVIS_STT_URL``,
``JARVIS_STT_LANGUAGE``, ``JARVIS_STT_TIMEOUT_S``). The first run after starting the
server can be slower (warm-up); compare the later ones. Exits 1 on any error.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
import wave

from app.stt.whisper_server import WhisperServerError, WhisperServerTranscriber


def check_format(data: bytes, name: str) -> float:
    """Return the duration in seconds; raise ValueError unless 16 kHz mono 16-bit (D6)."""

    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            frames = wf.getnframes()
    except (wave.Error, EOFError) as error:
        raise ValueError(f"{name}: not a PCM WAV ({error})") from error
    if (rate, channels, width) != (16000, 1, 2):
        raise ValueError(
            f"{name}: {rate} Hz, {channels} ch, {8 * width} bit; D6 needs 16000 Hz mono 16 bit "
            f"(ffmpeg -i {name} -ar 16000 -ac 1 -c:a pcm_s16le out.wav)"
        )
    return frames / rate


async def run(paths: list[str], runs: int) -> int:
    stt = WhisperServerTranscriber.from_env()
    print(f"whisper-server: {stt.url} (language {stt.language})")
    for path in paths:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            seconds = check_format(data, path)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        for i in range(1, runs + 1):
            started = time.perf_counter()
            try:
                text = await stt.transcribe(data)
            except WhisperServerError as error:
                print(f"error: {error}", file=sys.stderr)
                return 1
            ms = (time.perf_counter() - started) * 1000
            print(f"{path} ({seconds:.1f} s audio) run {i}: stt {ms:.0f} ms: {text!r}")
    return 0


def positive_int(raw: str) -> int:
    """argparse type for ``--runs``: an integer of 1 or more."""

    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be an integer of 1 or more, got {raw!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n")[0])
    parser.add_argument("wav", nargs="+", help="WAV PCM 16 kHz mono 16-bit")
    parser.add_argument(
        "--runs", type=positive_int, default=1, help="times to send each file (1 or more)"
    )
    args = parser.parse_args()
    return asyncio.run(run(args.wav, args.runs))


if __name__ == "__main__":
    sys.exit(main())
