"""``python -m jarvis.bench`` against a fake daemon: listing, switching, timing, exit codes."""

from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path

import pytest
from conftest import fake_server
from fake_gentle_dot import FakeDaemon

from jarvis import bench


def _run(tmp_path: Path, daemon: FakeDaemon | None, *args: str, token: bool = True) -> tuple[int, str, str]:
    token_file = tmp_path / "token"
    if token:
        token_file.write_text("t\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()

    async def scenario() -> int:
        if daemon is None:  # nothing listens on port 9
            return await bench.run(["--url", "ws://127.0.0.1:9/ws", "--token-file", str(token_file), *args], out, err)
        async with fake_server(daemon.handler) as port:
            argv = ["--url", f"ws://127.0.0.1:{port}/ws", "--token-file", str(token_file), *args]
            return await bench.run(argv, out, err)

    code = asyncio.run(scenario())
    return code, out.getvalue(), err.getvalue()


def test_list_prints_models_and_levels_and_sends_nothing(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    code, out, err = _run(tmp_path, daemon, "--list")
    assert code == 0, err
    assert "model: openai/gpt-5.4 (GPT-5.4) · thinking: medium" in out
    assert "thinking levels: off, minimal, low, medium, high, xhigh" in out
    assert "* openai/gpt-5.4" in out
    assert "openai/gpt-5.4-mini" in out and "off, minimal, low" in out
    assert re.search(r"openai/plain .*thinking: —", out)
    kinds = [m["type"] for m in daemon.received]
    assert "send" not in kinds and "model_set" not in kinds


def test_default_runs_three_prompts_and_prints_medians(tmp_path: Path) -> None:
    daemon = FakeDaemon(delays=(0.0, 0.05, 0.05))
    code, out, err = _run(tmp_path, daemon)
    assert code == 0, err
    sends = [m for m in daemon.received if m["type"] == "send"]
    assert sends == [{"type": "send", "text": bench.DEFAULT_PROMPT}] * 3
    runs = re.findall(r"run (\d)/3: echo ([\d.]+)s · first text ([\d.]+)s · total ([\d.]+)s", out)
    assert [r[0] for r in runs] == ["1", "2", "3"]
    for _, echo, first, total in runs:
        assert float(echo) <= float(first) <= float(total)
        assert float(first) >= 0.04 and float(total) >= 0.09
    assert re.search(r"median \(n=3\): echo [\d.]+s · first text [\d.]+s · total [\d.]+s", out)
    assert "model_set" not in [m["type"] for m in daemon.received]


def test_thinking_flag_sets_the_level_on_the_current_model_first(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    code, out, err = _run(tmp_path, daemon, "--thinking", "low", "-n", "1")
    assert code == 0, err
    kinds = [m["type"] for m in daemon.received]
    assert kinds.index("model_set") < kinds.index("send")
    assert {"type": "model_set", "provider": "openai", "id": "gpt-5.4", "thinking": "low"} in daemon.received
    assert "saved as the daemon's default" in out
    assert "model: openai/gpt-5.4 (GPT-5.4) · thinking: low" in out
    assert len(re.findall(r"run \d/1:", out)) == 1


def test_model_flag_switches_model(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    code, out, err = _run(tmp_path, daemon, "--model", "openai/gpt-5.4-mini", "--thinking", "off", "-n", "1")
    assert code == 0, err
    assert "model: openai/gpt-5.4-mini (GPT-5.4 mini) · thinking: off" in out
    assert daemon.model_id == "gpt-5.4-mini" and daemon.thinking == "off"


def test_list_with_a_switch_sets_then_lists_without_prompts(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    code, out, err = _run(tmp_path, daemon, "--list", "--thinking", "high")
    assert code == 0, err
    assert daemon.thinking == "high"
    assert "model: openai/gpt-5.4 (GPT-5.4) · thinking: high" in out
    assert "send" not in [m["type"] for m in daemon.received]


def test_unsupported_thinking_is_the_daemons_error_verbatim(tmp_path: Path) -> None:
    daemon = FakeDaemon()
    code, out, err = _run(tmp_path, daemon, "--model", "openai/gpt-5.4-mini", "--thinking", "high")
    assert code == bench.EXIT_DAEMON_ERROR
    assert "thinking_unavailable: That model does not support that thinking level." in err
    assert "send" not in [m["type"] for m in daemon.received]


def test_unknown_model_is_the_daemons_error(tmp_path: Path) -> None:
    code, _, err = _run(tmp_path, FakeDaemon(), "--model", "openai/nope")
    assert code == bench.EXIT_DAEMON_ERROR
    assert "model_unavailable:" in err


def test_send_refused_by_the_daemon(tmp_path: Path) -> None:
    code, _, err = _run(tmp_path, FakeDaemon(send_error="no_account"), "-n", "1")
    assert code == bench.EXIT_DAEMON_ERROR
    assert "no_account: Connect an AI account first." in err


def test_no_daemon_is_a_connection_error(tmp_path: Path) -> None:
    code, _, err = _run(tmp_path, None, "--list")
    assert code == bench.EXIT_CONNECT
    assert "Cannot reach the daemon" in err


def test_missing_token_is_a_connection_error_naming_the_path(tmp_path: Path) -> None:
    code, _, err = _run(tmp_path, FakeDaemon(), "--list", token=False)
    assert code == bench.EXIT_CONNECT
    assert "No access token at" in err and str(tmp_path / "token") in err


def test_a_reply_that_never_comes_times_out(tmp_path: Path) -> None:
    daemon = FakeDaemon(silent=True)
    code, _, err = _run(tmp_path, daemon, "-n", "1", "--timeout", "0.3")
    assert code == bench.EXIT_TIMEOUT
    assert "timed out" in err
    assert {"type": "abort"} in daemon.received  # does not leave the prompt running


def test_bad_arguments_are_usage_errors(tmp_path: Path) -> None:
    for args in (["--model", "no-slash"], ["--thinking", "turbo"], ["-n", "0"]):
        with pytest.raises(SystemExit) as raised:
            _run(tmp_path, FakeDaemon(), *args)
        assert raised.value.code == 2


def test_help_says_model_set_persists(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        bench.parse_args(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "persist" in text and "default" in text
    assert "exit codes" in text.lower()
