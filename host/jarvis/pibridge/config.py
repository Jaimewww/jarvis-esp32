"""Options of ``python -m jarvis.pibridge``: command line over ``JARVIS_PI_*`` over defaults."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .pi import PiSettings

#: ``host/`` of this checkout; the Jarvis extension and agent rules live under it.
REPO_HOST: Final = Path(__file__).resolve().parents[2]

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 4317
DEFAULT_TOKEN_PATH: Final = Path("~/.gentle-dot/token")
DEFAULT_EXECUTABLE: Final = "pi"
DEFAULT_MODEL: Final = "openai/gpt-6.1-sol"
DEFAULT_THINKING: Final = "low"
DEFAULT_PC_CONTROL: Final = REPO_HOST / "gentle-dot" / "extensions" / "pc-control.ts"
DEFAULT_AGENTS_MD: Final = REPO_HOST / "gentle-dot" / "AGENTS.md"
#: Replaces Pi's coding-assistant prompt; the Jarvis rules come from AGENTS.md.
DEFAULT_SYSTEM_PROMPT: Final = (
    "You are Jarvis, a voice assistant on the user's Linux desktop. "
    "Answer briefly, in the user's language."
)


@dataclass(frozen=True, slots=True)
class Config:
    host: str
    port: int
    token_path: Path
    pi: PiSettings
    log_level: str


def _parser(env: Mapping[str, str]) -> argparse.ArgumentParser:
    def default(name: str, fallback: str) -> str:
        return env.get(f"JARVIS_PI_{name}") or fallback

    parser = argparse.ArgumentParser(
        prog="python -m jarvis.pibridge",
        description=(
            "Speak the Gentle Dot WebSocket protocol v1 to Jarvis's clients and drive one "
            "'pi --mode rpc' process underneath. Every option also reads JARVIS_PI_<NAME> "
            "(for example JARVIS_PI_PORT); the command line wins."
        ),
    )
    parser.add_argument("--host", default=default("HOST", DEFAULT_HOST), help="address to bind (default %(default)s)")
    parser.add_argument("--port", type=int, default=default("PORT", str(DEFAULT_PORT)), help="port (default %(default)s)")
    parser.add_argument(
        "--token-file",
        type=Path,
        default=default("TOKEN_FILE", str(DEFAULT_TOKEN_PATH)),
        help="access token the clients present; created with mode 0600 when missing (default %(default)s)",
    )
    parser.add_argument("--pi", default=default("EXECUTABLE", DEFAULT_EXECUTABLE), help="Pi executable (default %(default)s)")
    parser.add_argument("--model", default=default("MODEL", DEFAULT_MODEL), help="provider/model (default %(default)s)")
    parser.add_argument("--thinking", default=default("THINKING", DEFAULT_THINKING), help="thinking level (default %(default)s)")
    parser.add_argument(
        "--pc-control",
        type=Path,
        default=default("PC_CONTROL", str(DEFAULT_PC_CONTROL)),
        help="the Jarvis extension (default %(default)s)",
    )
    parser.add_argument(
        "-e",
        "--extension",
        type=Path,
        action="append",
        default=[],
        help="extra Pi extension, repeatable; added to JARVIS_PI_EXTENSIONS (os.pathsep separated)",
    )
    parser.add_argument(
        "--agents-md",
        type=Path,
        default=default("AGENTS_MD", str(DEFAULT_AGENTS_MD)),
        help="Jarvis agent rules appended to the system prompt (default %(default)s)",
    )
    parser.add_argument(
        "--system-prompt",
        default=default("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        help="system prompt that replaces Pi's default",
    )
    parser.add_argument("--log-level", default=default("LOG_LEVEL", "INFO"), help="logging level (default %(default)s)")
    return parser


def parse_config(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    parser = _parser(env)
    args = parser.parse_args(argv)
    from_env = tuple(Path(p) for p in (env.get("JARVIS_PI_EXTENSIONS") or "").split(os.pathsep) if p)
    return Config(
        host=args.host,
        port=args.port,
        token_path=args.token_file,
        pi=PiSettings(
            executable=args.pi,
            model=args.model,
            thinking=args.thinking,
            pc_control=args.pc_control,
            extensions=from_env + tuple(args.extension),
            agents_md=args.agents_md,
            system_prompt=args.system_prompt,
        ),
        log_level=args.log_level.upper(),
    )
