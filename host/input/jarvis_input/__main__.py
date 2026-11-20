"""`python3 -m jarvis_input`: serve agent.sock and control.sock until SIGTERM (S13)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from .server import Helper, StartupError, run, runtime_dir_from_env


def main() -> int:
    os.umask(0o077)
    level = os.environ.get("JARVIS_INPUT_LOG", "INFO").upper()
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, level, logging.INFO),
                        format="jarvis-input %(levelname)s %(message)s")
    log = logging.getLogger("jarvis_input")
    try:
        helper = Helper(runtime_dir_from_env(os.environ))
        asyncio.run(run(helper))
    except StartupError as e:
        log.error("no arranco: %s", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
