"""S8: ``models_list`` answers from Pi's models; ``model_set`` applies them and is broadcast."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fake_pi import REASONING_LEVELS, FakeLauncher
from pibridge_harness import bridge_at, jarvis, run, wait_for

from jarvis.client import ErrorEvent, ModelChoice, ModelRef, ModelsEvent, Ready


def test_models_list_answers_with_the_current_model_and_its_thinking_levels(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running, jarvis(running) as probe:
            assert probe.client.models_list()
            await wait_for(lambda: bool(probe.of(ModelsEvent)))
            (models,) = probe.of(ModelsEvent)
            assert models.current == ModelRef("openai", "test-model", "Test Model", thinking="low")
            assert models.next is None
            assert [(g.provider, g.name) for g in models.groups] == [("openai", "openai"), ("anthropic", "anthropic")]
            assert models.groups[0].models == (
                ModelChoice("openai", "test-model", "Test Model", images=True, thinking_levels=tuple(REASONING_LEVELS)),
                ModelChoice("openai", "plain", "Plain", images=False, thinking_levels=()),
            )
            assert models.choice("anthropic", "claude-x") == ModelChoice(
                "anthropic", "claude-x", "Claude X", thinking_levels=tuple(REASONING_LEVELS))
            assert probe.host.agent_model == "Test Model" and probe.host.agent_thinking == "low"
            asked = {r["type"] for r in launcher.current.written}
            assert {"get_available_models", "get_state", "get_available_thinking_levels"} <= asked
            assert not probe.of(ErrorEvent)

    run(scenario())


def test_model_set_applies_model_and_thinking_and_every_client_hears_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as first, jarvis(running) as second:
                await wait_for(lambda: running.hub.size == 2)
                assert first.client.model_set("anthropic", "claude-x", "high")
                for probe in (first, second):
                    await wait_for(lambda p=probe: bool(p.of(ModelsEvent)))  # type: ignore[misc]
                    (models,) = probe.of(ModelsEvent)
                    assert models.current == ModelRef("anthropic", "claude-x", "Claude X", thinking="high")
                    assert probe.host.agent_model == "Claude X"
                pi = launcher.current
                sets = [r for r in pi.written if r["type"] in ("set_model", "set_thinking_level")]
                assert [{k: v for k, v in r.items() if k != "id"} for r in sets] == [
                    {"type": "set_model", "provider": "anthropic", "modelId": "claude-x"},
                    {"type": "set_thinking_level", "level": "high"},
                ]
                assert (pi.model["id"], pi.thinking) == ("claude-x", "high")
                # Without a thinking level only the model changes.
                assert first.client.model_set("openai", "test-model")
                await wait_for(lambda: len(second.of(ModelsEvent)) == 2)
                assert second.of(ModelsEvent)[1].current == ModelRef("openai", "test-model", "Test Model", thinking="high")
            async with jarvis(running) as late:
                (ready,) = late.of(Ready)
                assert ready.model == "openai/test-model"
            assert running.bridge.model == "openai/test-model"

    run(scenario())


def test_an_unknown_model_or_thinking_level_is_an_error_for_the_sender_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as asker, jarvis(running) as other:
                await wait_for(lambda: running.hub.size == 2)
                assert asker.client.model_set("openai", "nope")
                await wait_for(lambda: bool(asker.of(ErrorEvent)))
                assert asker.client.model_set("openai", "plain", "high")
                await wait_for(lambda: len(asker.of(ErrorEvent)) == 2)
                codes = [e.code for e in asker.of(ErrorEvent)]
                assert codes == ["model_unavailable", "thinking_unavailable"]
                assert all(e.message for e in asker.of(ErrorEvent))
                await asyncio.sleep(0.1)
                assert not other.of(ErrorEvent) and not other.of(ModelsEvent) and not asker.of(ModelsEvent)
                assert not [r for r in launcher.current.written if r["type"] in ("set_model", "set_thinking_level")]
                assert running.bridge.model == "openai/test-model"

    run(scenario())
