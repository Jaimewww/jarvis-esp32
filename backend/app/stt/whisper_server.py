"""``Transcriber`` (contract C3) backed by a local whisper.cpp ``whisper-server`` (D4).

The server keeps the model loaded; each clip is one ``POST <url>`` with a multipart
``file`` (the WAV PCM 16 kHz mono 16-bit of D6, which whisper-server reads without
``--convert``) plus ``response_format=json``, ``language`` and ``temperature``. It
answers ``{"text": " ..."}``. Checked against whisper.cpp 1.9.5 on CPU: a 1 s tone
gives ``200 {"text":" ¡Gracias!\\n"}``; a non-WAV upload gives ``400 Invalid request``
(plain text). Some failures come back as ``{"error": ...}``.

C3 defines no error model, so failures are raised as ``WhisperServerError``
subclasses whose message always names the URL.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Final

import httpx

ENV_URL: Final = "JARVIS_STT_URL"
ENV_LANGUAGE: Final = "JARVIS_STT_LANGUAGE"
ENV_TIMEOUT: Final = "JARVIS_STT_TIMEOUT_S"

DEFAULT_URL: Final = "http://127.0.0.1:8080/inference"
DEFAULT_LANGUAGE: Final = "es"
DEFAULT_TIMEOUT_S: Final = 30.0


class WhisperServerError(RuntimeError):
    """Base class: the clip could not be transcribed by whisper-server."""


class WhisperServerUnavailable(WhisperServerError):
    """No connection to whisper-server (not running, wrong host or port)."""


class WhisperServerTimeout(WhisperServerError):
    """whisper-server did not answer within the timeout."""


class WhisperServerHTTPError(WhisperServerError):
    """whisper-server answered with a status other than 200."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


class WhisperServerBadResponse(WhisperServerError):
    """The 200 answer is not JSON with a ``text`` string (or carries ``error``)."""


class WhisperServerTranscriber:
    """POSTs each clip to whisper-server and returns the stripped transcript."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        language: str = DEFAULT_LANGUAGE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.language = language
        self.timeout_s = timeout_s
        self._transport = transport  # tests inject httpx.MockTransport

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WhisperServerTranscriber:
        """Configure from ``JARVIS_STT_URL``, ``JARVIS_STT_LANGUAGE``, ``JARVIS_STT_TIMEOUT_S``."""

        env = os.environ if environ is None else environ
        return cls(
            env.get(ENV_URL) or DEFAULT_URL,
            language=env.get(ENV_LANGUAGE) or DEFAULT_LANGUAGE,
            timeout_s=float(env.get(ENV_TIMEOUT) or DEFAULT_TIMEOUT_S),
        )

    async def transcribe(self, wav: bytes) -> str:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout_s
            ) as client:
                response = await client.post(
                    self.url,
                    files={"file": ("clip.wav", wav, "audio/wav")},
                    data={
                        "response_format": "json",
                        "language": self.language,
                        "temperature": "0.0",
                    },
                )
        except httpx.TimeoutException as error:
            raise WhisperServerTimeout(
                f"whisper-server at {self.url} did not answer within {self.timeout_s} s"
            ) from error
        except httpx.TransportError as error:
            raise WhisperServerUnavailable(
                f"cannot reach whisper-server at {self.url} ({error!s}); "
                "is it running? (tools/whisper-server.sh)"
            ) from error

        if response.status_code != 200:
            raise WhisperServerHTTPError(
                f"whisper-server at {self.url} answered HTTP {response.status_code}: "
                f"{response.text.strip()[:200]}",
                status=response.status_code,
            )
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise WhisperServerBadResponse(
                f"whisper-server at {self.url} answered non-JSON: {response.text.strip()[:200]!r}"
            ) from error
        if isinstance(body, dict) and "error" in body:
            raise WhisperServerBadResponse(
                f"whisper-server at {self.url} reported an error: {body['error']}"
            )
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise WhisperServerBadResponse(
                f"whisper-server at {self.url} answered JSON without a 'text' string: "
                f"{response.text.strip()[:200]!r}"
            )
        return text.strip()
