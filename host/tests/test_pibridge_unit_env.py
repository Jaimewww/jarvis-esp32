"""S15.3 and S10: the variables of ``jarvis-pibridge.service.in`` reach the bridge and its Pi.

The names come from the unit template itself, so the unit and the option parser cannot drift:
every ``JARVIS_PI_*`` variable the template sets (or shows in its drop-in example) must change
what ``parse_config`` returns, and ``PI_CODING_AGENT_DIR`` (Pi's own agent directory, the only
Pi path the unit may write) must reach the Pi child's environment.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

from jarvis.pibridge.config import REPO_HOST, parse_config
from jarvis.pibridge.pi import PiSettings, subprocess_launcher

UNIT_TEMPLATE = REPO_HOST / "systemd" / "jarvis-pibridge.service.in"
#: Values the parser would refuse as text (``--port`` is an int, the timeout a float).
NUMERIC = {"JARVIS_PI_PORT": "4999", "JARVIS_PI_RECORD_TIMEOUT": "7.5", "JARVIS_PI_QUEUE_CAP": "5"}


def unit_environment() -> dict[str, str]:
    """``Environment=NAME=value`` lines of the template's ``[Service]``, not comments."""

    found: dict[str, str] = {}
    for line in UNIT_TEMPLATE.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"Environment=([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def mentioned_jarvis_pi_names() -> set[str]:
    """Every ``JARVIS_PI_*`` name in the template, including the drop-in example."""

    return set(re.findall(r"\bJARVIS_PI_[A-Z_]+\b", UNIT_TEMPLATE.read_text(encoding="utf-8")))


def test_the_template_sets_the_variables_this_test_relies_on() -> None:
    env = unit_environment()
    for name in ("JARVIS_PI_HOST", "JARVIS_PI_PORT", "JARVIS_PI_EXECUTABLE", "JARVIS_PI_PC_CONTROL",
                 "JARVIS_PI_AGENTS_MD", "PI_CODING_AGENT_DIR"):
        assert name in env, name
    # The drop-in example names these three; they must be read too.
    assert {"JARVIS_PI_MODEL", "JARVIS_PI_THINKING", "JARVIS_PI_EXTENSIONS"} <= mentioned_jarvis_pi_names()


@pytest.mark.parametrize("name", sorted(mentioned_jarvis_pi_names()))
def test_every_jarvis_pi_variable_of_the_unit_is_read_by_the_option_parser(name: str) -> None:
    value = NUMERIC.get(name, f"/sentinel/{name.lower()}")
    default = parse_config([], {})
    configured = parse_config([], {name: value})
    assert configured != default, f"{name} is not read by jarvis.pibridge"
    assert value in repr(configured), f"{name}={value} does not reach the configuration"


def test_the_unit_values_parse_once_the_placeholders_are_filled() -> None:
    env = {name: value.replace("@PI@", "/opt/pi/bin/pi").replace("@AGENT_HOME@", "/home/u/.gentle-dot/agent")
           for name, value in unit_environment().items() if name.startswith("JARVIS_PI_")}
    config = parse_config([], env)
    assert (config.host, config.port) == ("127.0.0.1", 4317)
    assert config.pi.executable == "/opt/pi/bin/pi"
    assert config.pi.pc_control == Path("/home/u/.gentle-dot/agent/extensions/jarvis-pc-control/index.ts")
    assert config.pi.agents_md == Path("/home/u/.gentle-dot/agent/AGENTS.md")


ENV_PI = r"""
import json, os, sys
for line in sys.stdin.buffer:
    record = json.loads(line)
    out = {"type": "response", "id": record.get("id"), "command": record.get("type"), "success": True,
           "data": {"PI_CODING_AGENT_DIR": os.environ.get("PI_CODING_AGENT_DIR")}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
"""


def test_pi_coding_agent_dir_reaches_the_pi_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "PI_CODING_AGENT_DIR" in unit_environment()
    agent_dir = str(tmp_path / "pi-agent")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", agent_dir)
    # A stand-in for pi: the launcher passes Pi's arguments, which the script ignores.
    fake_pi = tmp_path / "pi"
    fake_pi.write_text(f"#!{sys.executable} -I\n{ENV_PI}", encoding="utf-8")
    fake_pi.chmod(0o755)
    settings = PiSettings(executable=str(fake_pi), model="m/x", thinking="low", pc_control=tmp_path / "pc.ts",
                          extensions=(), agents_md=tmp_path / "AGENTS.md", system_prompt="p")

    async def scenario() -> dict[str, object]:
        pi = await subprocess_launcher(settings)()
        try:
            await pi.write({"id": "r1", "type": "get_state"})
            record = await asyncio.wait_for(pi.read(), 10)
        finally:
            await pi.close()
        assert record is not None
        return record

    record = asyncio.run(scenario())
    assert json.loads(json.dumps(record))["data"] == {"PI_CODING_AGENT_DIR": agent_dir}


def test_the_help_names_the_two_options_whose_variable_is_not_jarvis_pi_name(capsys: pytest.CaptureFixture[str]) -> None:
    # S16.4: "every option reads JARVIS_PI_<NAME>" is wrong for --pi and --extension.
    with pytest.raises(SystemExit):
        parse_config(["--help"], {})
    description = " ".join(capsys.readouterr().out.split("options:")[0].split())
    assert "--pi reads JARVIS_PI_EXECUTABLE" in description
    assert "--extension adds to JARVIS_PI_EXTENSIONS" in description
