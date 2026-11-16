"""Host-side client for the Gentle Dot daemon.

The TUI in this package talks to the daemon of the Gentle Dot fork
(``Jaimewww/gentle-dot``) over the WebSocket protocol in
``packages/protocol/src/index.ts`` (protocol version 1). This repo only holds the
client: it renders the chat and sends the user's words, it never runs the agent.
"""

from __future__ import annotations

__version__ = "0.1.0"
