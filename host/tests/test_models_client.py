"""Typed model helpers of the daemon client: ``models_list``, ``model_set``, ``models``."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from conftest import fake_server, wait_for
from fake_gentle_dot import FakeDaemon

from jarvis.client import (
    THINKING_LEVELS,
    ErrorEvent,
    JarvisClient,
    ModelChoice,
    ModelRef,
    ModelsEvent,
    parse_event,
    parse_model_spec,
)


def test_models_reply_is_parsed_into_typed_groups() -> None:
    event = parse_event(FakeDaemon().models_payload())
    assert isinstance(event, ModelsEvent)
    assert event.current == ModelRef("openai", "gpt-5.4", "GPT-5.4", "medium")
    assert event.next is None
    (group,) = event.groups
    assert group.provider == "openai" and group.name == "OpenAI"
    mini = group.models[1]
    assert mini == ModelChoice("openai", "gpt-5.4-mini", "GPT-5.4 mini", True, ("off", "minimal", "low"))
    assert group.models[2].thinking_levels == ()  # a model that does not think
    assert event.choice("openai", "gpt-5.4") is group.models[0]
    assert event.choice("openai", "nope") is None


def test_models_reply_without_current_and_with_bad_levels() -> None:
    event = parse_event(
        {"type": "models", "groups": [{"provider": "x", "name": "X", "models": [
            {"provider": "x", "id": "m", "name": "M", "thinkingLevels": ["low", "bogus", 3]}]}]}
    )
    assert isinstance(event, ModelsEvent)
    assert event.current is None
    assert event.groups[0].models[0].thinking_levels == ("low",)


def test_thinking_unavailable_error_is_surfaced_verbatim() -> None:
    message = "That model does not support that thinking level."
    event = parse_event({"type": "error", "code": "thinking_unavailable", "message": message})
    assert event == ErrorEvent("thinking_unavailable", message)


def test_parse_model_spec() -> None:
    assert parse_model_spec("openai/gpt-5.4-mini") == ("openai", "gpt-5.4-mini")
    for bad in ("gpt-5.4", "/x", "openai/", ""):
        with pytest.raises(ValueError):
            parse_model_spec(bad)


def test_thinking_levels_match_the_protocol() -> None:
    assert THINKING_LEVELS == ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def test_models_list_and_model_set_send_typed_frames(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("t\n", encoding="utf-8")
    daemon = FakeDaemon()
    events: list[Any] = []

    async def scenario() -> None:
        async with fake_server(daemon.handler) as port:
            client = JarvisClient(events.append, url=f"ws://127.0.0.1:{port}/ws", token_path=token)
            assert client.models_list() is False  # not connected yet
            task = asyncio.create_task(client.run())
            try:
                await wait_for(lambda: client.connected)
                assert client.models_list() is True
                await wait_for(lambda: any(isinstance(e, ModelsEvent) for e in events))
                assert client.model_set("openai", "gpt-5.4-mini", "off") is True
                await wait_for(lambda: sum(isinstance(e, ModelsEvent) for e in events) >= 2)
                assert client.model_set("openai", "gpt-5.4-mini", "high") is True
                await wait_for(lambda: any(isinstance(e, ErrorEvent) for e in events))
                with pytest.raises(ValueError):
                    client.model_set("openai", "gpt-5.4", "turbo")
            finally:
                await client.close()
                await task

    asyncio.run(scenario())
    sent = [m for m in daemon.received if m["type"] in ("models_list", "model_set")]
    assert sent == [
        {"type": "models_list"},
        {"type": "model_set", "provider": "openai", "id": "gpt-5.4-mini", "thinking": "off"},
        {"type": "model_set", "provider": "openai", "id": "gpt-5.4-mini", "thinking": "high"},
    ]
    models = [e for e in events if isinstance(e, ModelsEvent)]
    assert models[-1].current == ModelRef("openai", "gpt-5.4-mini", "GPT-5.4 mini", "off")
    error = next(e for e in events if isinstance(e, ErrorEvent))
    assert error == ErrorEvent("thinking_unavailable", "That model does not support that thinking level.")


def test_model_set_without_thinking_omits_the_field(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("t\n", encoding="utf-8")
    daemon = FakeDaemon()
    events: list[Any] = []

    async def scenario() -> None:
        async with fake_server(daemon.handler) as port:
            client = JarvisClient(events.append, url=f"ws://127.0.0.1:{port}/ws", token_path=token)
            task = asyncio.create_task(client.run())
            try:
                await wait_for(lambda: client.connected)
                client.model_set("openai", "plain")
                await wait_for(lambda: any(isinstance(e, ModelsEvent) for e in events))
            finally:
                await client.close()
                await task

    asyncio.run(scenario())
    assert {"type": "model_set", "provider": "openai", "id": "plain"} in daemon.received
