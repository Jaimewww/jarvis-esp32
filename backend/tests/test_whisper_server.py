"""``WhisperServerTranscriber`` (contract C3 ``Transcriber``) against fake whisper-servers.

The HTTP side is faked with ``httpx.MockTransport``; "server down" also uses a real
closed port so the actual connection error path is exercised.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from app.fakes import FakeTranscriber
from app.stt import make_transcriber
from app.stt.whisper_server import (
    DEFAULT_URL,
    WhisperServerBadResponse,
    WhisperServerError,
    WhisperServerHTTPError,
    WhisperServerTimeout,
    WhisperServerTranscriber,
    WhisperServerUnavailable,
)
from conftest import make_wav

URL = "http://whisper.test:8080/inference"


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


def transcriber(handler, **kwargs) -> WhisperServerTranscriber:
    return WhisperServerTranscriber(URL, transport=httpx.MockTransport(handler), **kwargs)


def test_success_returns_stripped_text_and_sends_multipart():
    seen: dict = {}
    wav = make_wav()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"text": " Hola, ¿qué hora es?\n"})

    text = run(transcriber(handler, language="es").transcribe(wav))
    assert text == "Hola, ¿qué hora es?"
    assert seen["method"] == "POST" and seen["url"] == URL
    assert seen["ctype"].startswith("multipart/form-data")
    body = seen["body"]
    assert b'name="file"; filename="clip.wav"' in body and wav in body
    assert b'name="language"\r\n\r\nes' in body
    assert b'name="response_format"\r\n\r\njson' in body


def test_server_down_names_the_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/inference"  # nothing listens here any more
    with pytest.raises(WhisperServerUnavailable) as err:
        run(WhisperServerTranscriber(url, timeout_s=2).transcribe(make_wav()))
    assert url in str(err.value)
    assert isinstance(err.value, WhisperServerError)


def test_non_200_names_url_and_status():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(WhisperServerHTTPError) as err:
        run(transcriber(handler).transcribe(make_wav()))
    assert err.value.status == 500
    assert URL in str(err.value) and "500" in str(err.value) and "boom" in str(err.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"no_text": 1}),
        httpx.Response(200, json=["text"]),
        httpx.Response(200, json={"text": 3}),
    ],
)
def test_bad_json_names_the_url(response):
    with pytest.raises(WhisperServerBadResponse) as err:
        run(transcriber(lambda request: response).transcribe(make_wav()))
    assert URL in str(err.value)


def test_error_field_from_server_is_reported():
    # whisper.cpp answers some failures (e.g. an unreadable WAV) with {"error": ...}.
    def handler(request):
        return httpx.Response(200, json={"error": "failed to read audio data"})

    with pytest.raises(WhisperServerBadResponse) as err:
        run(transcriber(handler).transcribe(make_wav()))
    assert "failed to read audio data" in str(err.value) and URL in str(err.value)


def test_timeout_names_url_and_limit():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(WhisperServerTimeout) as err:
        run(transcriber(handler, timeout_s=1.5).transcribe(make_wav()))
    assert URL in str(err.value) and "1.5" in str(err.value)


def test_from_env_defaults_and_overrides():
    t = WhisperServerTranscriber.from_env({})
    assert (t.url, t.language, t.timeout_s) == (DEFAULT_URL, "es", 30.0)
    assert DEFAULT_URL == "http://127.0.0.1:8080/inference"
    t = WhisperServerTranscriber.from_env(
        {
            "JARVIS_STT_URL": "http://10.0.0.2:9000/inference",
            "JARVIS_STT_LANGUAGE": "auto",
            "JARVIS_STT_TIMEOUT_S": "7.5",
        }
    )
    assert (t.url, t.language, t.timeout_s) == ("http://10.0.0.2:9000/inference", "auto", 7.5)


def test_make_transcriber_defaults_to_fake():
    assert isinstance(make_transcriber({}), FakeTranscriber)
    assert isinstance(make_transcriber({"JARVIS_STT": "fake"}), FakeTranscriber)
    t = make_transcriber({"JARVIS_STT": "whisper", "JARVIS_STT_URL": URL})
    assert isinstance(t, WhisperServerTranscriber) and t.url == URL


def test_make_transcriber_rejects_unknown_value():
    with pytest.raises(ValueError, match="JARVIS_STT"):
        make_transcriber({"JARVIS_STT": "parakeet"})

