"""Sign-in frames of protocol v1: typed events in, typed requests out (no real account)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from conftest import fake_server, wait_for

from jarvis.client import (
    AuthDoneEvent,
    AuthFlowEvent,
    AuthLink,
    AuthOption,
    AuthPrompt,
    AuthPromptEvent,
    AuthProvider,
    AuthProvidersEvent,
    JarvisClient,
    parse_event,
)


def test_auth_providers_become_typed_providers_and_unknown_fields_are_ignored() -> None:
    event = parse_event(
        {
            "type": "auth_providers",
            "seq": 4,
            "open": True,
            "voice": {"available": False},
            "providers": [
                {"id": "anthropic", "name": "Anthropic", "methods": ["api_key"], "configured": False,
                 "colour": "ignored"},
                {"id": "openai-codex", "name": "ChatGPT", "methods": ["oauth", "api_key", 7],
                 "oauthName": "ChatGPT Plus/Pro", "configured": True, "source": "stored"},
                "not a provider",
            ],
        }
    )
    assert event == AuthProvidersEvent(
        providers=(
            AuthProvider("anthropic", "Anthropic", ("api_key",), configured=False),
            AuthProvider("openai-codex", "ChatGPT", ("oauth", "api_key"), configured=True,
                         oauth_name="ChatGPT Plus/Pro", source="stored"),
        ),
        open=True,
    )
    assert parse_event({"type": "auth_providers", "providers": []}) == AuthProvidersEvent((), open=False)


def test_auth_prompt_of_every_kind() -> None:
    secret = parse_event(
        {"type": "auth_prompt", "prompt": {"flowId": "f1", "kind": "secret", "message": "API key",
                                           "placeholder": "sk-…", "extra": 1}}
    )
    assert secret == AuthPromptEvent(AuthPrompt("f1", "secret", "API key", placeholder="sk-…"))
    select = parse_event(
        {"type": "auth_prompt", "prompt": {"flowId": "f1", "kind": "select", "message": "Pick one",
                                           "optional": True,
                                           "options": [{"id": "a", "label": "A", "description": "first"},
                                                       {"id": "b", "label": "B"}, {"label": "no id"}]}}
    )
    assert select == AuthPromptEvent(
        AuthPrompt("f1", "select", "Pick one", optional=True,
                   options=(AuthOption("a", "A", "first"), AuthOption("b", "B")))
    )
    for kind in ("text", "manual_code"):
        event = parse_event({"type": "auth_prompt", "prompt": {"flowId": "f2", "kind": kind, "message": "m"}})
        assert event == AuthPromptEvent(AuthPrompt("f2", kind, "m"))
    # A prompt without its object or of an unknown kind cannot be answered: dropped.
    assert parse_event({"type": "auth_prompt"}) is None
    assert parse_event({"type": "auth_prompt", "prompt": {"flowId": "f", "kind": "voice", "message": "m"}}) is None


def test_auth_events_of_every_kind() -> None:
    def ev(event: dict[str, Any]) -> Any:
        return parse_event({"type": "auth_event", "flowId": "f1", "event": event})

    assert ev({"kind": "info", "message": "Hi", "links": [{"url": "https://x.test", "label": "X"}, {"url": "https://y.test"}]}) == AuthFlowEvent(
        "f1", "info", message="Hi", links=(AuthLink("https://x.test", "X"), AuthLink("https://y.test"))
    )
    assert ev({"kind": "auth_url", "url": "https://auth.test/a", "instructions": "Open it"}) == AuthFlowEvent(
        "f1", "auth_url", url="https://auth.test/a", instructions="Open it"
    )
    assert ev({"kind": "device_code", "userCode": "ABCD-1234", "verificationUri": "https://dev.test",
               "expiresInSeconds": 900}) == AuthFlowEvent(
        "f1", "device_code", user_code="ABCD-1234", verification_uri="https://dev.test", expires_in_seconds=900
    )
    assert ev({"kind": "progress", "message": "Waiting…"}) == AuthFlowEvent("f1", "progress", message="Waiting…")
    assert ev({"kind": "telepathy"}) is None
    assert parse_event({"type": "auth_event", "flowId": "f1"}) is None


def test_auth_done() -> None:
    assert parse_event({"type": "auth_done", "flowId": "f1", "providerId": "openai", "ok": True}) == AuthDoneEvent(
        "f1", "openai", ok=True
    )
    assert parse_event(
        {"type": "auth_done", "flowId": "f1", "providerId": "openai", "ok": False, "message": "Sign-in cancelled."}
    ) == AuthDoneEvent("f1", "openai", ok=False, message="Sign-in cancelled.")


def test_sends_every_auth_request(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("daemon-token\n", encoding="utf-8")
    received: list[dict[str, Any]] = []

    async def handler(socket: Any) -> None:
        await socket.recv()  # hello
        await socket.send(json.dumps({"type": "ready", "seq": 1, "agentState": "idle"}))
        async for raw in socket:
            received.append(json.loads(raw))

    async def scenario() -> None:
        async with fake_server(handler) as port:
            client = JarvisClient(lambda _: None, url=f"ws://127.0.0.1:{port}/ws", token_path=token_file)
            assert not client.auth_list()  # not connected yet: nothing queued
            task = asyncio.create_task(client.run())
            try:
                await wait_for(lambda: client.connected)
                assert client.auth_list()
                assert client.auth_login("openai", "api_key")
                assert client.auth_reply("f1", value="typed")
                assert client.auth_reply("f1", cancelled=True)
                assert client.auth_logout("openai")
                await wait_for(lambda: len(received) >= 8)
            finally:
                await client.close()
                await task

    asyncio.run(scenario())
    assert received[3:] == [
        {"type": "auth_list"},
        {"type": "auth_login", "providerId": "openai", "method": "api_key"},
        {"type": "auth_reply", "flowId": "f1", "value": "typed"},
        {"type": "auth_reply", "flowId": "f1", "cancelled": True},
        {"type": "auth_logout", "providerId": "openai"},
    ]
