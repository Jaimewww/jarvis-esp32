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
* `jarvis/tui.py` — Textual app: both connections, the two views in one window (F2),
  dark and light mode (F3), the input row and the footer.
* `jarvis/model.py` — what both views show, without any UI toolkit: devices, links,
  agent, voice turns with their stages, typed conversation, PC activity per message,
  and the derived figures (stage medians, lane targets, over-target, lane verdicts).
* `jarvis/userview.py` — the user view (approved mockup
  `design/tui-user/08-story-chat-polished.html`), the default.
* `jarvis/devview.py` — the developer view (approved mockup
  `design/tui-developer/06-pipeline-refined.html`).
* `jarvis/theme.py` — the six colour themes of `design/tui-theme` as tokens (copied
  by value; nothing under `design/` is read at run time). Both views draw only
  through them.
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
python -m jarvis.tui --theme paper            # start in another theme (see below)
python -m jarvis.tui --log-level debug        # client log on stderr (noisy under Textual)
```

The window opens two independent connections: the Gentle Dot daemon (conversation and
actions) and the Jarvis backend (`ws://127.0.0.1:8000/api/v1/ws` by default; ESP32
state and voice turns). Either can be down without hiding the other. The backend
token is sent in the URL query and is never shown in the window or the log. A refused
token shows `✗ backend unauthorized` in the developer view and stops retrying; restart
with the right token.

To see it without a board, start the backend and run the simulator from the repo root:

```bash
python tools/sim_device.py --url http://127.0.0.1:8000
```

Keys (all listed in the footer): **Enter** sends the text in the input row, **Ctrl+A**
stops the answer that is running, **Ctrl+Q** quits, **F2** switches between the user
view and the developer view, **F3** switches between dark and light mode.

Without a daemon the client retries with backoff (500 ms, 1 s, 2 s, 5 s) with no
traceback and no freeze. The user view says that Gentle Dot is not running (or has no
access token yet) and keeps showing the voice side; the developer view shows
`○ daemon disconnected` in its top border.

## Dark and light mode

Both views share one theme, copied from the approved proposals in `design/tui-theme`
(tokens: background, surface, message, border, dim, text, text 2, muted, accent and
its gradient, and the four state colours live, ok, over target and failed). Colour
marks state only; the accent is the Jarvis mark `◆`, the `▸` prompt, the large
letters, the card pulse and every bar and meter.

The window starts in the dark theme **Gentleman Cute**; **F3** switches to the light
theme **Paper** and back. `--theme <name>` starts in any of the six — `teal`, `plum`,
`graphite`, `paper`, `gentleman-cute`, `gentleman-sexy` — and F3 then pairs it with
the other mode (a dark start with Paper, Paper with Gentleman Cute). The default dark
theme is one constant, `DEFAULT_DARK` in `jarvis/theme.py`.

## What the window shows

**User view** (the default):

| Part | Content |
|---|---|
| Jarvis card | the agent's state in large letters (`READY`, `THINKING`, `WORKING`, `NEEDS YOU`, `LISTENING`, `SPEAKING`, `STARTING`, `ERROR`, `OFFLINE`), a spinner and a pulse on the card's edge while it works, what it is working on, and how many messages wait their turn |
| Voice device card | the device and its state in plain words (`Ready`, `Listening`, `Thinking`, `Speaking`, `Offline`…) with a hint, a level meter, the connection to this PC and the signal as bars (good, fair, weak, poor) |
| Thread | today's exchanges, newest at the bottom, on a timeline rail: time and `said ◎` / `typed ▸`, the user's words right-aligned in a bubble, the answer under `◆`, and what Jarvis did "on your PC" (`✓` done, `◐` running, `○` in the background, `✗` failed); a failed exchange says what to do next; the live one has an accent rail, a bold answer and `ctrl+a stops it`. `Today · N earlier above` counts the exchanges that do not fit |
| Input and footer | the `▸ Message Jarvis` row and the keys |

Voice exchanges come from the backend's turns (heard text, reply, catalogue actions,
failure); typed ones from the daemon's messages and their activity, merged by time.
If the daemon repeats a voice turn's words as a user message (as it would when Gentle
Dot answers voice turns), the echo folds into the voice exchange instead of showing
twice. The user view shows no developer detail: no turn ids, timings, stage or lane
names, dBm or addresses. The level meter is drawn, not measured: contract C2 carries
no audio level, so it moves while the device listens or speaks. Below 100 columns the
two cards stack and the rail drops the `said`/`typed` word; when the Jarvis card is too
narrow for a state's large letters and its two facts (about 110 columns for `WORKING`
side by side, 75 stacked), the state is a plain bold word instead. Daemon toasts, errors and `ask` notices, and a
message that could not be sent, appear as short notifications.

**Developer view** (F2): a rounded card with the device, its state, dBm and IP, and
both connections in its top border, and the live turn as a four-stage pipeline (stt,
route, agent, tts) with the median of each stage and a pulse under the live stage;
the finished voice turns newest first with a gradient bar against the lane target
(fast 1.5 s, chat 4.0 s, task none) and the amount over it, and the action (`→`) and
reply under each; a summary per lane; the typed conversation (`◆ jarvis`); and a card
with the agent and its PC actions. Both views are fed by every event, so switching
always shows current data. Two gaps of contract C2 show on screen: an `action` has no
`turn_id`, so it is listed under the open turn of the same device; the lane of a live
turn is only known when it ends (`lane —` until then). The backend does not emit
`turn_stage` or `turn_error` yet, so stage figures and heard text stay empty against
it today (the user view shows `you spoke` in the bubble).

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
