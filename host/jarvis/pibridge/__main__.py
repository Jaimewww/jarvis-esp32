"""``python -m jarvis.pibridge``: the bridge daemon in the foreground."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from .config import Config, parse_config
from .pi import subprocess_launcher
from .server import BridgeServer, Hub
from .token import TokenError, load_or_create_token
from .turns import Bridge

logger = logging.getLogger("jarvis.pibridge")


async def serve(config: Config) -> None:
    token = load_or_create_token(config.token_path)
    hub = Hub()
    bridge = Bridge(subprocess_launcher(config.pi), hub.broadcast, model=config.pi.model)
    server = BridgeServer(token=token, agent=bridge, hub=hub)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await bridge.start()
    await server.start(config.host, config.port)
    logger.info("token file %s, model %s:%s", config.token_path.expanduser(), config.pi.model, config.pi.thinking)
    try:
        await stop.wait()
    finally:
        await server.close()
        await bridge.close()


def main(argv: list[str] | None = None) -> int:
    config = parse_config(argv)
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(serve(config))
    except TokenError as error:
        print(f"pibridge: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"pibridge: cannot listen on {config.host}:{config.port}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
