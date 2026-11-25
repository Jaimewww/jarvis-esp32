"""S12: the resilience findings of the T1 review, one test (or more) per item."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest
from fake_pi import FakeLauncher, asks, hang, reply, silent
from pibridge_harness import bridge_at, raw, run, wait_for
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from jarvis.pibridge import __main__ as bridge_main
from jarvis.pibridge import token as token_module
from jarvis.pibridge.config import REPO_HOST, parse_config
from jarvis.pibridge.pi import SubprocessPi
from jarvis.pibridge.server import Hub
from jarvis.pibridge.token import TokenError, load_or_create_token
from jarvis.pibridge.turns import Bridge

Frames = list[dict[str, Any]]


def recorder() -> tuple[Frames, Any]:
    frames: Frames = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    return frames, emit


def idle(frames: Frames) -> int:
    return frames.count({"type": "agent_state", "state": "idle"})


# --- 1. A slow client -----------------------------------------------------------------


class FakeConnection:
    """A connection whose ``send`` never returns when ``stuck`` (a client that stopped reading)."""

    def __init__(self, *, stuck: bool) -> None:
        self.stuck = stuck
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None

    async def send(self, message: str) -> None:
        if self.stuck:
            await asyncio.Event().wait()
        self.sent.append(json.loads(message))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


def test_a_slow_client_stalls_neither_the_turn_nor_the_other_client() -> None:
    async def scenario() -> None:
        hub = Hub(max_pending=4)
        slow, fast = FakeConnection(stuck=True), FakeConnection(stuck=False)
        await hub.admit(slow, {"type": "ready"})  # type: ignore[arg-type]
        await hub.admit(fast, {"type": "ready"})  # type: ignore[arg-type]
        bridge = Bridge(FakeLauncher(reply("uno ", "dos ", "tres ", "cuatro")), hub.broadcast, model="openai/m")
        await asyncio.wait_for(bridge.send("cuenta"), 1)
        await wait_for(lambda: {"type": "agent_state", "state": "idle"} in [
            {k: v for k, v in f.items() if k != "seq"} for f in fast.sent])
        assert bridge.agent_state == "idle"
        assert [f["seq"] for f in fast.sent] == list(range(1, len(fast.sent) + 1))
        assert [f["type"] for f in fast.sent][-2:] == ["message_done", "agent_state"]
        # The slow one fell more than max_pending frames behind: it is dropped and closed.
        await wait_for(lambda: slow.closed is not None)
        assert slow.closed is not None and slow.closed[0] == 1008
        assert hub.size == 1
        await bridge.close()

    run(scenario())


# --- 2. A port already taken ------------------------------------------------------------

FAKE_PI = """#!/bin/sh
echo $$ > {marker}
exec sleep 60
"""


def test_a_failed_bind_never_starts_pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = FakeLauncher()
    monkeypatch.setattr(bridge_main, "subprocess_launcher", lambda _settings: launcher)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        config = parse_config(["--port", str(port), "--token-file", str(tmp_path / "token")], {})
        with pytest.raises(bridge_main.ListenError, match=f"127.0.0.1:{port}"):
            asyncio.run(bridge_main.serve(config))
    assert launcher.launches == []


def test_a_failed_bind_exits_non_zero_names_the_address_and_leaves_no_pi(tmp_path: Path) -> None:
    marker = tmp_path / "pi-started"
    fake_pi = tmp_path / "pi"
    fake_pi.write_text(FAKE_PI.format(marker=marker))
    fake_pi.chmod(stat.S_IRWXU)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_PI_")}
        result = subprocess.run(
            [sys.executable, "-m", "jarvis.pibridge", "--port", str(port), "--pi", str(fake_pi),
             "--token-file", str(tmp_path / "token")],
            cwd=REPO_HOST, env=env, capture_output=True, text=True, timeout=30, check=False,
        )
    assert result.returncode == 1
    assert f"cannot listen on 127.0.0.1:{port}" in result.stderr
    assert not marker.exists(), "Pi was started although the bridge could not listen"


# --- 3. Pi's output ends or overflows ----------------------------------------------------

CLOSES_STDOUT = "import os, time\nos.close(1)\ntime.sleep(30)\n"
OVERSIZED = (
    "import json, sys\n"
    "sys.stdout.write('{\"type\": \"big\", \"data\": \"' + 'x' * 5000 + '\"}\\n')\n"
    "sys.stdout.write(json.dumps({'type': 'after'}) + '\\n')\n"
    "sys.stdout.flush()\n"
    "sys.stdin.read()\n"
)


def test_eof_with_pi_still_running_is_bounded_and_ends_pi() -> None:
    async def scenario() -> None:
        pi = await SubprocessPi.spawn([sys.executable, "-I", "-c", CLOSES_STDOUT], grace=0.2)
        assert await asyncio.wait_for(pi.read(), 3) is None
        assert pi.exit_code is not None  # stopped, not left running
        await pi.close()

    run(scenario())


def test_an_oversized_record_is_skipped_and_the_stream_continues(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        pi = await SubprocessPi.spawn([sys.executable, "-I", "-c", OVERSIZED], limit=1024)
        assert await asyncio.wait_for(pi.read(), 5) == {"type": "after"}
        assert pi.exit_code is None
        await pi.close()

    run(scenario())
    assert "larger than 1024 bytes" in caplog.text


# --- 4. A stuck turn, a pending question, the queue cap ----------------------------------


def test_a_turn_with_no_pi_record_in_time_ends_with_an_error_and_pi_restarts() -> None:
    async def scenario() -> None:
        frames, emit = recorder()
        launcher = FakeLauncher(silent("Abriendo"), reply("Hecho."))
        bridge = Bridge(launcher, emit, model="openai/m", record_timeout=0.3)
        await bridge.send("abre firefox")
        await wait_for(lambda: idle(frames) == 1)
        stuck = launcher.launches[0]
        assert [f["type"] for f in frames] == [
            "agent_state", "user_message", "message_delta", "message_done", "error", "agent_state"]
        done, error = frames[3], frames[4]
        assert done["text"] == "Abriendo" and done["error"]
        assert error["code"] == "agent_timeout" and "0.3" in error["message"]
        assert bridge.agent_state == "idle"
        await wait_for(lambda: stuck.closed)
        await bridge.send("otra vez")
        await wait_for(lambda: idle(frames) == 2)
        assert len(launcher.launches) == 2
        assert [f["text"] for f in frames if f["type"] == "message_done"][-1] == "Hecho."
        await bridge.close()

    run(scenario())


def test_a_pending_question_does_not_count_as_a_stuck_turn() -> None:
    async def scenario() -> None:
        frames, emit = recorder()
        launcher = FakeLauncher(asks("confirm", final="Hecho.", title="Run it?"))
        bridge = Bridge(launcher, emit, model="openai/m", record_timeout=0.2)
        await bridge.clients_changed(1)
        await bridge.send("hazlo")
        await wait_for(lambda: any(f["type"] == "ask" for f in frames))
        await asyncio.sleep(0.6)  # three timeouts with the person thinking
        assert not [f for f in frames if f["type"] == "error"]
        await bridge.answer("ui-1", {"confirmed": True})
        await wait_for(lambda: idle(frames) == 1)
        assert not [f for f in frames if f["type"] == "error"]
        assert len(launcher.launches) == 1
        await bridge.close()

    run(scenario())


def test_a_send_beyond_the_queue_cap_gets_an_error_for_its_sender(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(hang("..."), reply("b"))
        async with bridge_at(launcher, tmp_path / "token", queue_cap=1) as running:
            async with raw(running) as asker, raw(running) as other:
                for text in ("a", "b", "c"):
                    await asker.send(json.dumps({"type": "send", "text": text}))
                seen: Frames = []
                while not any(f["type"] == "error" for f in seen):
                    seen.append(json.loads(await asyncio.wait_for(asker.recv(), 3)))
                error = next(f for f in seen if f["type"] == "error")
                assert error["code"] == "queue_full" and error["message"]
                queues = [f["followUp"] for f in seen if f["type"] == "queue"]
                assert queues == [["b"]]
                others: Frames = []
                with pytest.raises(TimeoutError):
                    while True:
                        others.append(json.loads(await asyncio.wait_for(other.recv(), 0.3)))
                assert not [f for f in others if f["type"] == "error"]
                assert await running.bridge.send("d") is False
                prompts = [r["message"] for r in launcher.current.written if r["type"] == "prompt"]
                assert prompts == ["a"]

    run(scenario())


def test_the_timeout_and_the_queue_cap_are_options_with_env_equivalents() -> None:
    config = parse_config([], {})
    assert (config.record_timeout, config.queue_cap) == (120.0, 16)
    config = parse_config([], {"JARVIS_PI_RECORD_TIMEOUT": "30", "JARVIS_PI_QUEUE_CAP": "3"})
    assert (config.record_timeout, config.queue_cap) == (30.0, 3)
    config = parse_config(["--record-timeout", "5.5", "--queue-cap", "2"], {"JARVIS_PI_QUEUE_CAP": "3"})
    assert (config.record_timeout, config.queue_cap) == (5.5, 2)
    result = subprocess.run([sys.executable, "-m", "jarvis.pibridge", "--help"], cwd=REPO_HOST,
                            capture_output=True, text=True, timeout=30, check=False)
    for text in ("--record-timeout", "--queue-cap", "JARVIS_PI_RECORD_TIMEOUT", "JARVIS_PI_QUEUE_CAP"):
        assert text in result.stdout


# --- 5. Two bridges create the token at once --------------------------------------------


def _racing_os(make: Any) -> types.SimpleNamespace:
    """``os`` for the token module where the exclusive create loses to another writer."""

    def lose(path: str | Path, *_: Any) -> int:
        make(Path(path))
        raise FileExistsError(path)

    return types.SimpleNamespace(open=lose, fdopen=os.fdopen, O_WRONLY=os.O_WRONLY, O_CREAT=os.O_CREAT,
                                 O_EXCL=os.O_EXCL)


def test_the_other_writers_unreadable_file_is_a_token_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_module, "os", _racing_os(lambda p: p.mkdir()))
    with pytest.raises(TokenError, match="access token"):
        load_or_create_token(tmp_path / "token")


def test_the_other_writers_file_is_read_once_it_has_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def make(path: Path) -> None:
        path.write_text("")
        threading.Timer(0.1, lambda: path.write_text("their-token\n")).start()

    monkeypatch.setattr(token_module, "os", _racing_os(make))
    assert load_or_create_token(tmp_path / "token") == "their-token"


def test_a_token_race_is_reported_as_a_token_error_not_a_listen_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(token_module, "os", _racing_os(lambda p: p.mkdir()))
    code = bridge_main.main(["--token-file", str(tmp_path / "token"), "--port", "0", "--pi", "/nonexistent/pi"])
    err = capsys.readouterr().err
    assert code == 2
    assert "access token" in err and "cannot listen" not in err


# --- 6. Browsers -----------------------------------------------------------------------


def test_a_connection_with_an_origin_header_is_refused_before_the_hello(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        async with bridge_at(launcher, tmp_path / "token") as running:
            for origin in ("http://evil.example", "null"):
                with pytest.raises(InvalidStatus) as refused:
                    async with connect(running.url, origin=origin):  # type: ignore[arg-type]
                        pass
                assert refused.value.response.status_code == 403
            assert running.hub.size == 0 and not launcher.launches
            # Jarvis's clients send no Origin (websockets adds one only when asked): admitted.
            async with raw(running):
                assert running.hub.size == 1

    run(scenario())
