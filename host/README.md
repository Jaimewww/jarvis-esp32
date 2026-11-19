# Host TUI — client of the Gentle Dot daemon

`host/jarvis` is the computer-side window described in [docs/tui.md](../docs/tui.md).
It is a **client**: it shows the conversation and what the agent did on the PC, and it
sends what the user types. The agent, the tools, and the approvals live in the Gentle
Dot fork (`Jaimewww/gentle-dot`, branch `feat/linux-computer-use`), which this repo
does not vendor.

* `jarvis/client.py` — WebSocket client for the daemon protocol v1: hello + token,
  reconnect with backoff, typed events (`ready`, `agent_state`, `user_message`,
  `message_delta`, `message_done`, `activity`, `ask`, `ask_resolved`, `toast`,
  `history`, `earlier`, `conversations`, `queue`, `error`), `send`/`steer`/`abort`,
  and a `get_history` repair when a `seq` gap shows a frame was lost.
* `jarvis/backend.py` — read-only WebSocket client for the Jarvis backend (contract C2
  in [docs/contratos.md](../docs/contratos.md)): ESP32 state and voice-turn events,
  reconnect with backoff, unknown event types and fields ignored.
* `jarvis/tui.py` — Textual app: conversation, agent-state chip, ESP32 line, voice
  turns, action log, input, connection indicators.
* `requirements.txt` — `websockets` and `textual`, both `>=`.
* `requirements-dev.txt` and `tests/` — pytest suite against in-process fake
  WebSocket servers; no daemon, backend, or board needed.

## Requirements

* Python 3.11 or newer.
* A running daemon of the fork.

```bash
cd host
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## The daemon must be running

The TUI does not start or replace the daemon. Start it from the fork:

```bash
git clone https://github.com/Jaimewww/gentle-dot
cd gentle-dot && git switch feat/linux-computer-use
pnpm install
pnpm dev            # daemon on http://127.0.0.1:4317
```

The daemon binds `127.0.0.1` only and writes a random access token to
`~/.gentle-dot/token` (mode `0600`). The client reads that file; nothing is typed in
the chat and the token never appears in the UI.

## Running it

```bash
cd host
python -m jarvis.tui                          # ws://127.0.0.1:4317/ws, ~/.gentle-dot/token
python -m jarvis.tui --port 4318              # another port
python -m jarvis.tui --url ws://127.0.0.1:4317/ws
python -m jarvis.tui --token-file /path/to/token
python -m jarvis.tui --backend-url ws://192.168.1.10:8000/api/v1/ws
python -m jarvis.tui --backend-token <token>  # default: $JARVIS_HOST_TOKEN, then dev-host-token
python -m jarvis.tui --log-level debug        # client log on stderr (noisy under Textual)
```

The window opens two independent connections: the Gentle Dot daemon (conversation and
actions) and the Jarvis backend (`ws://127.0.0.1:8000/api/v1/ws` by default; ESP32
state and voice turns). Either can be down without hiding the other. The backend
token is sent in the URL query and is never shown in the window or the log. A refused
token shows `BACKEND UNAUTHORIZED` and stops retrying; restart with the right token.

To see it without a board, start the backend and run the simulator from the repo root:

```bash
python tools/sim_device.py --url http://127.0.0.1:8000
```

Keys: **Enter** sends the text in the input box, **Ctrl+A** stops the answer that is
running, **Ctrl+Q** quits.

Without a daemon the client retries with backoff (500 ms, 1 s, 2 s, 5 s) with no
traceback and no freeze: the status line shows `LINK DISCONNECTED` and the detail line
says why (`connection refused (is the daemon running?)`, `No access token at …`).

## What the window shows

| Part | Content |
|---|---|
| Status line | agent state (`IDLE`, `THINKING`, `WORKING`, `NEEDS YOU`, `RESTARTING`, `ERROR`, `STARTING`), link state (`CONNECTED`, `CONNECTING`, `DISCONNECTED`, `UNAUTHORIZED`, `BAD PROTOCOL`), model, conversation title, queued messages |
| ESP32 line | per device: id and state (`IDLE`, `RECORDING`, … or `OFFLINE`), signal in dBm, IP; then the backend link state |
| Detail line | the last daemon connection message, for example a refused token or a protocol mismatch |
| Conversation | the user's messages and the assistant's answer, streamed as it arrives; the latest page of the chat's history on connect |
| Voice turns | one line per backend event: turn started, each stage with its milliseconds, catalogue actions, turn done (lane, total time, reply) or failed, and backend link changes |
| Action log | `activity` frames (kind, what it did, running/done/failed), toasts, server errors, and the connection's own messages |
| Input | one line of text; sent with Enter |

What it deliberately does not do:

* **It never answers an `ask` approval.** Approvals are shown by the desktop app over
  its private channel, and this window only writes that one is waiting. A `pin` ask on
  a server is answered in the browser.
* No file attachments, no attachments upload, no conversation switching, no sign-in
  flows, and no paging back past the first history page.

## Tests

```bash
cd host
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## Security caveat: the catalog is bounded in the backend

The TUI is display-only and executes nothing. The guarantee that only a bounded,
known set of computer actions runs — and that unknown actions are refused — is
enforced in the fork: the grant, panic stop, blocklist, rate limit, risky-action
confirmations, and the read-only tool list behind the MCP proxy. This window is not a
security boundary, and a TUI that shows an action must never be read as evidence that
the action was authorized.

## Computer use on Linux (from the fork)

Computer control on Wayland is a keys-and-apps catalog, in the fork branch
`feat/linux-computer-use`:

* screenshots through `grim` (wlroots screencopy, as in niri),
* keys and text through `wtype`, multimedia keys included (volume, brightness),
* applications through their installed `.desktop` entries and `gtk-launch`,
* no pointer: clicks, drags, and scrolling are refused.

Install `wtype` and `grim` (`pacman -S wtype grim`, or the Debian/Ubuntu
equivalents) and restart the Gentle Dot app. What the catalog allows, and every
approval it asks, stay in the fork; see its `apps/desktop/src-tauri/src/computer/`
module.

## Protocol v1 in one paragraph

`ws://host:port/ws`; the first frame is
`{"type": "hello", "token": "<token>", "protocol": 1}`. The daemon answers `ready`
with the agent state, model, conversation, and features, and closes with `4401` when
the token is wrong and `4400` when the protocol version is not supported. Every frame
from the daemon carries a per-connection monotonic `seq`. The client asks for
`get_history`, `list_conversations`, and `auth_list` once `ready` arrives, and sends
`send`, `steer`, and `abort`. The message shapes are the ones in the fork's
`packages/protocol/src/index.ts`, the same ones the desktop app and the web UI use.
