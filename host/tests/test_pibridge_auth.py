"""S3: the Pi bridge admits a client only after a hello with the right token (protocol v1)."""

from __future__ import annotations

import asyncio
import json
import logging
import stat
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from jarvis.pibridge.server import BridgeServer, Hub
from jarvis.pibridge.token import TokenError, load_or_create_token, token_matches

T = TypeVar("T")
TOKEN = "s3cr3t-bridge-token-for-tests"
#: The test's own client sends the token; only the bridge's logs are under test.
QUIET = logging.getLogger("tests.pibridge.client")
QUIET.setLevel(logging.INFO)


def run(coro: Awaitable[T]) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(coro, 10)

    return asyncio.run(bounded())


class StubAgent:
    """The turn side of the bridge, reduced to what the auth tests need."""

    agent_state = "idle"
    model = "openai/test-model"

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.aborts = 0

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def abort(self) -> None:
        self.aborts += 1

    async def answer(self, request_id: str, response: dict[str, Any]) -> None:
        pass

    async def models(self) -> dict[str, Any]:
        return {"type": "models", "groups": []}

    async def set_model(self, provider: str, model_id: str, thinking: str | None) -> dict[str, Any]:
        return await self.models()

    async def clients_changed(self, count: int) -> None:
        pass


async def first_reply(url: str, first: str | None) -> tuple[int | None, list[dict[str, Any]]]:
    """Send ``first`` (or nothing) and return the close code and the frames received."""

    frames: list[dict[str, Any]] = []
    async with connect(url, logger=QUIET) as socket:
        if first is not None:
            await socket.send(first)
        try:
            while True:
                frames.append(json.loads(await socket.recv()))
                if frames[-1].get("type") == "ready":
                    return None, frames
        except ConnectionClosed as error:
            close = error.rcvd or error.sent
            return (close.code if close else None), frames


def hello(token: Any = TOKEN, protocol: Any = 1) -> str:
    return json.dumps({"type": "hello", "token": token, "protocol": protocol})


async def with_server(agent: StubAgent, hello_timeout: float = 5.0):
    server = BridgeServer(token=TOKEN, agent=agent, hub=Hub(), hello_timeout=hello_timeout)
    port = await server.start("127.0.0.1", 0)
    return server, f"ws://127.0.0.1:{port}/ws"


# --- Token file ----------------------------------------------------------------------


def test_creates_a_missing_token_file_with_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "dir" / "token"
    token = load_or_create_token(path)
    assert len(token) >= 32
    assert path.read_text(encoding="utf-8").strip() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_or_create_token(path) == token  # an existing file is read, not replaced


def test_reads_an_existing_token_file_without_changing_its_mode(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("existing-token\n", encoding="utf-8")
    path.chmod(0o640)
    assert load_or_create_token(path) == "existing-token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_an_empty_token_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(TokenError) as raised:
        load_or_create_token(path)
    assert str(path) in str(raised.value)


def test_token_comparison_is_constant_time_and_rejects_non_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.pibridge.token as module

    calls: list[tuple[bytes, bytes]] = []
    real = module.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(module.hmac, "compare_digest", spy)
    assert token_matches(TOKEN, TOKEN)
    assert not token_matches(TOKEN, TOKEN + "x")
    assert not token_matches(TOKEN, None)
    assert not token_matches(TOKEN, 123)
    assert len(calls) == 2  # every string comparison goes through compare_digest


# --- Hello -----------------------------------------------------------------------------


def test_the_right_token_gets_ready_with_seq_one() -> None:
    async def scenario() -> None:
        agent = StubAgent()
        server, url = await with_server(agent)
        try:
            code, frames = await first_reply(url, hello())
        finally:
            await server.close()
        assert code is None
        assert len(frames) == 1
        ready = frames[0]
        assert ready["type"] == "ready"
        assert ready["seq"] == 1
        assert ready["agentState"] == "idle"
        assert ready["model"] == "openai/test-model"
        assert ready["features"] == {"conversations": False}
        assert TOKEN not in json.dumps(ready)

    run(scenario())


@pytest.mark.parametrize(
    ("first", "expected"),
    [
        (hello(token="wrong-token"), 4401),
        (hello(token=None), 4401),
        (json.dumps({"type": "hello", "protocol": 1}), 4401),
        (hello(token=TOKEN.encode().hex()), 4401),
        (hello(protocol=2), 4400),
        (hello(protocol="1"), 4400),
        ("not json", 4400),
        (json.dumps(["hello"]), 4400),
        (json.dumps({"type": "send", "text": "hola"}), 4400),
    ],
    ids=[
        "wrong-token",
        "null-token",
        "missing-token",
        "other-token",
        "protocol-2",
        "protocol-string",
        "not-json",
        "not-an-object",
        "send-before-hello",
    ],
)
def test_a_bad_first_frame_closes_with_the_code_the_clients_map(first: str, expected: int) -> None:
    async def scenario() -> None:
        agent = StubAgent()
        server, url = await with_server(agent)
        try:
            code, frames = await first_reply(url, first)
        finally:
            await server.close()
        assert code == expected
        assert frames == []  # nothing, not even ready, before the close
        assert agent.sent == []  # a send before hello never reaches the agent

    run(scenario())


def test_no_hello_in_time_closes_with_4401() -> None:
    async def scenario() -> None:
        agent = StubAgent()
        server, url = await with_server(agent, hello_timeout=0.2)
        try:
            code, frames = await first_reply(url, None)
        finally:
            await server.close()
        assert code == 4401
        assert frames == []

    run(scenario())


def test_frames_after_ready_reach_the_agent() -> None:
    async def scenario() -> None:
        agent = StubAgent()
        server, url = await with_server(agent)
        try:
            async with connect(url) as socket:
                await socket.send(hello())
                assert json.loads(await socket.recv())["type"] == "ready"
                await socket.send(json.dumps({"type": "send", "text": "hola"}))
                await socket.send(json.dumps({"type": "abort"}))
                await socket.send("garbage")  # ignored, the link stays up
                for _ in range(100):
                    if agent.aborts:
                        break
                    await asyncio.sleep(0.01)
        finally:
            await server.close()
        assert agent.sent == ["hola"]
        assert agent.aborts == 1

    run(scenario())


def test_broadcast_numbers_frames_per_connection() -> None:
    async def scenario() -> None:
        agent = StubAgent()
        hub = Hub()
        server = BridgeServer(token=TOKEN, agent=agent, hub=hub)
        port = await server.start("127.0.0.1", 0)
        url = f"ws://127.0.0.1:{port}/ws"
        try:
            async with connect(url) as socket:
                await socket.send(hello())
                await socket.recv()
                await hub.broadcast({"type": "agent_state", "state": "working"})
                await hub.broadcast({"type": "agent_state", "state": "idle"})
                second = json.loads(await socket.recv())
                third = json.loads(await socket.recv())
        finally:
            await server.close()
        assert (second["seq"], second["state"]) == (2, "working")
        assert (third["seq"], third["state"]) == (3, "idle")

    run(scenario())


def test_the_token_never_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        agent = StubAgent()
        server, url = await with_server(agent)
        try:
            await first_reply(url, hello())
            await first_reply(url, hello(token=TOKEN + "-wrong"))
            await first_reply(url, hello(protocol=7))
        finally:
            await server.close()

    run(scenario())
    assert caplog.records  # the server did log something
    # websockets cuts long frames in its debug lines, so look for any piece of the token.
    pieces = {TOKEN[i : i + 8] for i in range(len(TOKEN) - 7)}
    for record in caplog.records:
        message = record.getMessage()
        assert not any(piece in message for piece in pieces), record.name


def test_a_token_that_cannot_be_encoded_is_just_wrong() -> None:
    # A lone surrogate is valid JSON text but not UTF-8: it must be a mismatch, not a crash.
    assert token_matches("secret", "\ud800") is False
    assert token_matches("secret", "secret") is True


def test_a_token_file_others_can_read_is_used_with_a_warning_that_omits_the_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "token"
    path.write_text("shared-secret\n", encoding="utf-8")
    path.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        assert load_or_create_token(path) == "shared-secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644  # an existing file is never changed
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and str(path) in warnings[0] and "0644" in warnings[0]
    assert "shared-secret" not in caplog.text


def test_a_private_token_file_is_read_without_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "token"
    path.write_text("own-secret\n", encoding="utf-8")
    path.chmod(0o600)
    with caplog.at_level(logging.WARNING):
        assert load_or_create_token(path) == "own-secret"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize(("host", "warned"), [("127.0.0.1", False), ("localhost", False), ("0.0.0.0", True)])
def test_binding_outside_loopback_warns(host: str, warned: bool, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        server = BridgeServer(token="secret", agent=StubAgent(), hub=Hub())
        await server.start(host, 0)
        await server.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    hits = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "loopback" in r.getMessage()]
    assert (len(hits) == 1) is warned
