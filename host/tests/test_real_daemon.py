"""The host client and window against the REAL Gentle Dot daemon (S3). Opt-in.

Skipped unless ``JARVIS_REAL_DAEMON=1``, so CI (which has no daemon) stays green. Run
with the daemon up, for example as the ``gentle-dot.service`` user service from
``host/systemd/``::

    cd host
    JARVIS_REAL_DAEMON=1 python -m pytest tests/test_real_daemon.py -v -rs

The daemon is the default one (``ws://127.0.0.1:4317/ws``, token from
``~/.gentle-dot/token``) unless ``JARVIS_DAEMON_URL`` / ``JARVIS_DAEMON_TOKEN_FILE``
say otherwise. The token is opened by the client and never printed. Only the last
test sends a message (one short prompt); without an AI account the daemon refuses it
and the window must show the daemon's own message. Nothing here signs in: the
``auth_list`` test only reads the account list.
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from typing import Any

import pytest
from conftest import wait_for

from jarvis.client import DEFAULT_TOKEN_PATH, AuthProvidersEvent, ConnectionStatus, JarvisClient, Ready
from jarvis.devview import DeveloperView
from jarvis.tui import JarvisApp
from jarvis.userview import UserView

pytestmark = pytest.mark.skipif(
    os.environ.get("JARVIS_REAL_DAEMON") != "1",
    reason="real Gentle Dot daemon tests are opt-in: set JARVIS_REAL_DAEMON=1",
)

URL = os.environ.get("JARVIS_DAEMON_URL") or "ws://127.0.0.1:4317/ws"
TOKEN_PATH = Path(os.environ.get("JARVIS_DAEMON_TOKEN_FILE") or DEFAULT_TOKEN_PATH).expanduser()
#: What upstream f97d56d answers a ``send`` with when no AI account is connected.
NO_ACCOUNT = "Connect an AI account first: open Accounts or type /login."


def free_port() -> int:
    """A local port nothing listens on: the window runs without a backend."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_real_client_connects_and_gets_ready() -> None:
    events: list[Any] = []
    statuses: list[ConnectionStatus] = []

    async def scenario() -> None:
        client = JarvisClient(events.append, lambda s, _d: statuses.append(s), url=URL, token_path=TOKEN_PATH)
        task = asyncio.create_task(client.run())
        try:
            await wait_for(lambda: client.connected and any(isinstance(e, Ready) for e in events), timeout=10)
        finally:
            await client.close()
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
    assert ConnectionStatus.CONNECTED in statuses
    ready = next(e for e in events if isinstance(e, Ready))
    assert ready.agent_state  # e.g. "idle"


def test_real_window_connects_to_the_daemon(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str]:
        app = JarvisApp(
            url=URL,
            token_path=TOKEN_PATH,
            backend_url=f"ws://127.0.0.1:{free_port()}/api/v1/ws",  # no backend: only the daemon is checked
            backend_token="t",
            animate=False,
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_for(lambda: app._host.daemon_status is ConnectionStatus.CONNECTED, timeout=10)
            await pilot.pause()
            user = app.query_one(UserView).render().plain
            await pilot.press("f2")
            await pilot.pause()
            return user, app.query_one(DeveloperView).render().plain

    user, developer = asyncio.run(scenario())
    assert "daemon connected" in developer
    assert "Gentle Dot is not running" not in user and "isn't set up yet" not in user


def test_real_window_typing_shows_the_answer_or_the_daemons_message() -> None:
    async def scenario() -> tuple[str | None, str]:
        app = JarvisApp(
            url=URL,
            token_path=TOKEN_PATH,
            backend_url=f"ws://127.0.0.1:{free_port()}/api/v1/ws",
            backend_token="t",
            animate=False,
        )
        text = "Jarvis test: answer with one short sentence that says OK."
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_for(lambda: app._host.daemon_status is ConnectionStatus.CONNECTED, timeout=10)
            await pilot.click("#prompt")
            await pilot.press(*text, "enter")
            host = app._host
            await wait_for(
                lambda: host.agent_notice is not None or any(m.text == text for m in host.messages),
                timeout=30,
            )
            await pilot.pause()
            notice = host.agent_notice
            if notice is None:
                await pilot.press("ctrl+a")  # do not leave the test prompt running
                await pilot.pause()
            return notice, app.query_one(UserView).render().plain

    notice, user = asyncio.run(scenario())
    if notice is None:
        pytest.skip("Gentle Dot has an AI account connected: the prompt was accepted and echoed")
    assert notice == NO_ACCOUNT
    assert NO_ACCOUNT in user


def test_real_daemon_lists_its_accounts_read_only() -> None:
    """Only ``auth_list`` (sent on connect): no ``auth_login``, ``auth_reply`` or ``auth_logout``."""

    events: list[Any] = []

    async def scenario() -> None:
        client = JarvisClient(events.append, url=URL, token_path=TOKEN_PATH)
        task = asyncio.create_task(client.run())
        try:
            await wait_for(lambda: any(isinstance(e, AuthProvidersEvent) for e in events), timeout=10)
        finally:
            await client.close()
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
    listed = next(e for e in events if isinstance(e, AuthProvidersEvent))
    assert all(p.id and p.name and p.methods for p in listed.providers)
    # Upstream offers Anthropic with an API key only.
    for provider in listed.providers:
        if provider.id == "anthropic":
            assert "oauth" not in provider.methods
