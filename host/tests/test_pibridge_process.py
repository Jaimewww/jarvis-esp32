"""S5 and S2: the Pi command line, the real child-process wrapper and the daemon's options."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.pibridge.config import REPO_HOST, parse_config
from jarvis.pibridge.pi import PiSettings, SubprocessPi, pi_command

def settings(**overrides: object) -> PiSettings:
    base: dict[str, object] = {
        "executable": "/opt/pi/bin/pi",
        "model": "openai/gpt-6.1-sol",
        "thinking": "low",
        "pc_control": Path("/repo/host/gentle-dot/extensions/pc-control.ts"),
        "extensions": (Path("/x/pi-claude-bridge"),),
        "agents_md": Path("/repo/host/gentle-dot/AGENTS.md"),
        "system_prompt": "Short Jarvis prompt.",
    }
    base.update(overrides)
    return PiSettings(**base)  # type: ignore[arg-type]


def value_after(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == flag]


def test_the_command_line_is_the_measured_one_with_the_configured_paths() -> None:
    argv = pi_command(settings())
    assert argv[0] == "/opt/pi/bin/pi"
    assert value_after(argv, "--mode") == ["rpc"]
    for flag in ("--no-session", "-ne", "-ns", "-np", "-nc", "--no-themes"):
        assert argv.count(flag) == 1, flag
    assert value_after(argv, "--model") == ["openai/gpt-6.1-sol:low"]
    assert value_after(argv, "-e") == [
        "/repo/host/gentle-dot/extensions/pc-control.ts",
        "/x/pi-claude-bridge",
    ]
    assert value_after(argv, "--append-system-prompt") == ["/repo/host/gentle-dot/AGENTS.md"]
    assert value_after(argv, "--system-prompt") == ["Short Jarvis prompt."]


def test_without_extra_extensions_only_pc_control_is_loaded() -> None:
    argv = pi_command(settings(extensions=(), model="anthropic/claude-x", thinking="high"))
    assert value_after(argv, "-e") == ["/repo/host/gentle-dot/extensions/pc-control.ts"]
    assert value_after(argv, "--model") == ["anthropic/claude-x:high"]


# --- Config ----------------------------------------------------------------------------


def test_defaults_bind_localhost_4317_and_point_at_the_repo_files() -> None:
    config = parse_config([], {})
    assert (config.host, config.port) == ("127.0.0.1", 4317)
    assert config.token_path == Path("~/.gentle-dot/token")
    pi = config.pi
    assert (pi.executable, pi.model, pi.thinking) == ("pi", "openai/gpt-6.1-sol", "low")
    assert pi.pc_control == REPO_HOST / "gentle-dot" / "extensions" / "pc-control.ts"
    assert pi.agents_md == REPO_HOST / "gentle-dot" / "AGENTS.md"
    assert pi.pc_control.is_file() and pi.agents_md.is_file()
    assert pi.extensions == ()
    assert pi.system_prompt


def test_environment_then_options_override_the_defaults(tmp_path: Path) -> None:
    env = {
        "JARVIS_PI_HOST": "127.0.0.2",
        "JARVIS_PI_PORT": "5000",
        "JARVIS_PI_TOKEN_FILE": str(tmp_path / "env-token"),
        "JARVIS_PI_EXECUTABLE": "/env/pi",
        "JARVIS_PI_MODEL": "env/model",
        "JARVIS_PI_THINKING": "medium",
        "JARVIS_PI_EXTENSIONS": "/a/one.ts:/b/two",
        "JARVIS_PI_PC_CONTROL": "/env/pc.ts",
        "JARVIS_PI_AGENTS_MD": "/env/AGENTS.md",
        "JARVIS_PI_SYSTEM_PROMPT": "env prompt",
    }
    config = parse_config([], env)
    assert (config.host, config.port, config.token_path) == ("127.0.0.2", 5000, tmp_path / "env-token")
    assert config.pi == PiSettings(
        executable="/env/pi",
        model="env/model",
        thinking="medium",
        pc_control=Path("/env/pc.ts"),
        extensions=(Path("/a/one.ts"), Path("/b/two")),
        agents_md=Path("/env/AGENTS.md"),
        system_prompt="env prompt",
    )
    config = parse_config(
        ["--port", "6000", "--model", "cli/model", "--thinking", "off", "-e", "/c/three.ts",
         "--token-file", str(tmp_path / "cli-token"), "--pi", "/cli/pi"],
        env,
    )
    assert config.port == 6000
    assert config.token_path == tmp_path / "cli-token"
    assert (config.pi.executable, config.pi.model, config.pi.thinking) == ("/cli/pi", "cli/model", "off")
    assert config.pi.extensions == (Path("/a/one.ts"), Path("/b/two"), Path("/c/three.ts"))


def test_help_describes_the_options() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jarvis.pibridge", "--help"],
        cwd=REPO_HOST,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    for option in ("--host", "--port", "--token-file", "--pi", "--model", "--thinking",
                   "--extension", "--pc-control", "--agents-md", "--system-prompt"):
        assert option in result.stdout


def test_a_bad_port_is_refused() -> None:
    with pytest.raises(SystemExit):
        parse_config([], {"JARVIS_PI_PORT": "nope"})


# --- The real child process ------------------------------------------------------------

ECHO_PI = r"""
import json, sys
for line in sys.stdin.buffer:
    record = json.loads(line)
    if record.get("type") == "quit":
        sys.exit(5)
    out = {"type": "response", "id": record.get("id"), "command": record.get("type"),
           "success": True, "data": {"text": "a\u2028b"}}
    sys.stdout.write("not json\n")
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
"""


def test_the_subprocess_wrapper_speaks_json_lines_and_reports_the_exit() -> None:
    async def scenario() -> None:
        pi = await SubprocessPi.spawn([sys.executable, "-I", "-c", ECHO_PI])
        assert pi.exit_code is None
        await pi.write({"id": "r1", "type": "get_state"})
        record = await asyncio.wait_for(pi.read(), 5)
        # Non-JSON lines are skipped; U+2028 inside a string does not split a record.
        assert record == {"type": "response", "id": "r1", "command": "get_state",
                          "success": True, "data": {"text": "a\u2028b"}}
        await pi.write({"type": "quit"})
        assert await asyncio.wait_for(pi.read(), 5) is None
        assert pi.exit_code == 5
        with pytest.raises(OSError):
            await pi.write({"type": "get_state"})
        await pi.close()

    asyncio.run(scenario())


def test_closing_the_subprocess_ends_it() -> None:
    async def scenario() -> None:
        pi = await SubprocessPi.spawn([sys.executable, "-I", "-c", ECHO_PI])
        await pi.close()
        assert pi.exit_code == 0  # stdin closed: the child finished on its own

    asyncio.run(scenario())
