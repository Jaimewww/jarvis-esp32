"""The existing Gentle Dot daemon client still behaves as before."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from conftest import fake_server, wait_for

from jarvis.client import ConnectionStatus, JarvisClient, Ready, UserMessageEvent


def test_says_hello_with_the_token_then_requests_state_and_parses_events(
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("daemon-token\n", encoding="utf-8")
    received: list[dict[str, Any]] = []
    events: list[Any] = []

    async def handler(socket: Any) -> None:
        received.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({"type": "ready", "seq": 1, "agentState": "idle"}))
        for _ in range(3):
            received.append(json.loads(await socket.recv()))
        await socket.send(
            json.dumps({"type": "user_message", "seq": 2, "messageId": "m1", "text": "hola"})
        )
        await socket.wait_closed()

    async def scenario() -> None:
        async with fake_server(handler) as port:
            client = JarvisClient(
                events.append, url=f"ws://127.0.0.1:{port}/ws", token_path=token_file
            )
            task = asyncio.create_task(client.run())
            try:
                await wait_for(lambda: len(events) >= 2)
                assert client.status is ConnectionStatus.CONNECTED
            finally:
                await client.close()
                await task

    asyncio.run(scenario())

    assert received == [
        {"type": "hello", "token": "daemon-token", "protocol": 1},
        {"type": "get_history"},
        {"type": "list_conversations"},
        {"type": "auth_list"},
    ]
    assert events == [Ready(agent_state="idle"), UserMessageEvent("m1", "hola")]
