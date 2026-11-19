"""Shared helpers: in-process fake WebSocket servers, no daemon or backend needed."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

Handler = Callable[[Any], Awaitable[None]]


@asynccontextmanager
async def fake_server(handler: Handler):
    """Serve ``handler`` on a free local port and yield that port."""

    async with serve(handler, "127.0.0.1", 0) as server:
        yield server.sockets[0].getsockname()[1]


def frame(message: dict[str, Any]) -> bytes:
    """One backend frame: JSON as UTF-8 in a binary frame (contract C2)."""

    return json.dumps(message).encode("utf-8")


async def wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    """Poll until ``condition`` holds; fail the test when it never does."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)
