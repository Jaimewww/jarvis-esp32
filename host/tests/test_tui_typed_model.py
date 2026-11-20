"""The window: optional wanted model/thinking on connect, and the last reply's timing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from conftest import fake_server, wait_for
from fake_gentle_dot import FakeDaemon

from jarvis.devview import DeveloperView
from jarvis.tui import JarvisApp, parse_args
from jarvis.userview import UserView


async def _idle_backend(socket: Any) -> None:
    await socket.wait_closed()


def _window(tmp_path: Path, daemon: FakeDaemon, *, prompt: str | None = None, **kwargs: Any) -> dict[str, str]:
    token = tmp_path / "token"
    token.write_text("t\n", encoding="utf-8")

    async def scenario() -> dict[str, str]:
        async with fake_server(_idle_backend) as port, fake_server(daemon.handler) as daemon_port:
            app = JarvisApp(
                url=f"ws://127.0.0.1:{daemon_port}/ws",
                token_path=token,
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
                **kwargs,
            )
            async with app.run_test(size=(140, 40)) as pilot:
                host = app._host
                await wait_for(lambda: host.agent_thinking is not None, timeout=5.0)
                await asyncio.sleep(0.2)  # room for a model_set, if one were sent
                if prompt is not None:
                    await pilot.click("#prompt")
                    await pilot.press(*prompt, "enter")
                    await wait_for(lambda: host.last_timing is not None, timeout=5.0)
                await pilot.pause()
                user = app.query_one(UserView).render().plain
                await pilot.press("f2")
                await pilot.pause()
                return {"user": user, "developer": app.query_one(DeveloperView).render().plain}

    return asyncio.run(scenario())


def _model_sets(daemon: FakeDaemon) -> list[dict[str, Any]]:
    return [m for m in daemon.received if m["type"] == "model_set"]


def test_by_default_the_window_never_changes_the_model(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    views = _window(tmp_path, daemon)
    assert {"type": "models_list"} in daemon.received  # read-only: shows the thinking level
    assert _model_sets(daemon) == []
    assert "GPT-5.4 · medium" in views["developer"]


def test_a_wanted_thinking_level_is_applied_when_it_differs(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    views = _window(tmp_path, daemon, typed_thinking="low")
    assert _model_sets(daemon) == [{"type": "model_set", "provider": "openai", "id": "gpt-5.4", "thinking": "low"}]
    assert "GPT-5.4 · low" in views["developer"]


def test_a_wanted_model_is_applied_when_it_differs(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    _window(tmp_path, daemon, typed_model="openai/gpt-5.4-mini", typed_thinking="minimal")
    assert _model_sets(daemon) == [
        {"type": "model_set", "provider": "openai", "id": "gpt-5.4-mini", "thinking": "minimal"}
    ]


def test_nothing_is_sent_when_the_wanted_model_is_already_running(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    _window(tmp_path, daemon, typed_model="openai/gpt-5.4", typed_thinking="medium")
    assert _model_sets(daemon) == []


def test_developer_view_shows_the_last_replys_timing(tmp_path: Path) -> None:
    daemon = FakeDaemon(delays=(0.0, 0.1, 0.1))
    views = _window(tmp_path, daemon, prompt="hola")
    line = next(line for line in views["developer"].splitlines() if " reply " in line)
    assert "first text 0." in line and "· total 0." in line
    assert "first text" not in views["user"]  # the user view stays as it was


def test_flags_and_environment_choose_the_wanted_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_TYPED_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_TYPED_THINKING", raising=False)
    args = parse_args([])
    assert args.model is None and args.thinking is None
    monkeypatch.setenv("JARVIS_TYPED_MODEL", "openai/gpt-5.4-mini")
    monkeypatch.setenv("JARVIS_TYPED_THINKING", "minimal")
    args = parse_args([])
    assert (args.model, args.thinking) == ("openai/gpt-5.4-mini", "minimal")
    args = parse_args(["--model", "openai/gpt-5.4", "--thinking", "low"])
    assert (args.model, args.thinking) == ("openai/gpt-5.4", "low")
    for bad in (["--model", "noslash"], ["--thinking", "turbo"]):
        with pytest.raises(SystemExit):
            parse_args(bad)
    monkeypatch.setenv("JARVIS_TYPED_THINKING", "turbo")
    with pytest.raises(SystemExit):
        parse_args([])
