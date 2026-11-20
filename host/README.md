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

The TUI does not start or replace the daemon. To run the upstream daemon as a
systemd user service instead, see
[Daemon de Gentle Dot como servicio](#daemon-de-gentle-dot-como-servicio). For a
quick manual run from the fork:

```bash
git clone https://github.com/Jaimewww/gentle-dot
cd gentle-dot && git switch feat/linux-computer-use
pnpm install
pnpm dev            # daemon on http://127.0.0.1:4317
```

The daemon binds `127.0.0.1` only and writes a random access token to
`~/.gentle-dot/token` (mode `0600`). The client reads that file; nothing is typed in
the chat and the token never appears in the UI.

## Daemon de Gentle Dot como servicio

Según [D1](../docs/decisiones.md#d1--gentle-dot-es-una-dependencia-externa), Jarvis usa
el daemon **original** de Gentle Dot, sin modificar y fijado a una versión, como
servicio en segundo plano y sin la aplicación de escritorio (tarea P3). El proyecto
original no trae un archivo de servicio; este repositorio trae uno de usuario de
systemd:

* `systemd/gentle-dot.service.in` — plantilla de la unidad. Arranca
  `node packages/daemon/src/cli.ts` (lo mismo que `pnpm start` del proyecto original),
  antes corre su `--self-check`, se reinicia ante fallos con espera creciente (2 s a
  1 min, como mucho 10 arranques en 10 minutos) y escribe en el journal. Sin secretos.
* `systemd/install.sh` — instala, verifica o quita la unidad. Solo escribe
  `~/.config/systemd/user/gentle-dot.service` (o bajo `$XDG_CONFIG_HOME`) y solo llama a
  `systemctl --user`. Se niega a correr como root.
* `systemd/test_install.sh` — pruebas del script sin systemd ni Gentle Dot (ver abajo).

### Requisitos

* systemd con sesión de usuario (`systemctl --user` funciona). La unidad usa
  `RestartSteps=` y `RestartMaxDelaySec=` (systemd 254 o superior).
* Node 24 o superior, con la ruta del **binario real**: un shim de mise, asdf o volta
  no sirve, porque el servicio no tiene el directorio ni la shell de los que el shim
  elige la versión. Con mise: `$(mise where node@24)/bin/node`.
* El checkout del proyecto original en el commit fijado, con sus dependencias:

  ```bash
  git clone https://github.com/Gentleman-Programming/gentle-dot
  cd gentle-dot
  git switch --detach f97d56d884b1d3c178f415b95d486464e7b220bf
  pnpm install --frozen-lockfile
  pnpm build          # opcional: interfaz web (la TUI no la necesita)
  ```

  El script rechaza un checkout en otro commit (salvo `--allow-unpinned`) o sin
  `node_modules`.
* Para que arranque **sin iniciar sesión** (al encender la PC), opcional y fuera del
  script: `loginctl enable-linger "$USER"`. Sin eso arranca al iniciar sesión, que es lo
  que pide P3.

### Instalar

Desde la raíz del repositorio, primero sin cambiar nada:

```bash
host/systemd/install.sh --gentle-dot-dir ~/ruta/gentle-dot \
  --node "$(mise where node@24)/bin/node" --check     # solo verifica requisitos
host/systemd/install.sh --gentle-dot-dir ~/ruta/gentle-dot \
  --node "$(mise where node@24)/bin/node" --dry-run   # muestra la unidad y los comandos
```

Y después de verdad:

```bash
host/systemd/install.sh --gentle-dot-dir ~/ruta/gentle-dot \
  --node "$(mise where node@24)/bin/node" --enable
```

Sin `--enable` solo escribe la unidad y hace `systemctl --user daemon-reload`; con
`--enable` además corre `systemctl --user enable --now gentle-dot.service` (y
`try-restart` si la unidad cambió). Repetirlo es seguro: si la unidad ya está al día no
hace nada.

- `--allow-dirty`: acepta un checkout de Gentle Dot con cambios sin commitear o archivos sin seguimiento (desaconsejado). Sin ella, el instalador se niega y no escribe nada si `git -C <checkout> status --porcelain` no está vacío; `--check` también lo comprueba.
- Backup: antes de reemplazar una unidad distinta, el instalador guarda la anterior en `~/.config/systemd/user/gentle-dot.service.bak` e imprime la ruta. `--uninstall` la conserva.
- Límite de arranques: la unidad admite como mucho 10 arranques en 10 minutos (`StartLimitIntervalSec=10min`, `StartLimitBurst=10`); después queda en `failed` hasta `systemctl --user reset-failed gentle-dot.service`.
- Si `systemctl --user daemon-reload` falla, el instalador sale con error y deja `gentle-dot.service.reload-pending`; vuelve a ejecutarlo (o `--uninstall`) y reintentará la recarga en vez de responder "Sin cambios".

### Verificar

```bash
systemctl --user status gentle-dot.service
journalctl --user -u gentle-dot.service -f
ls -l ~/.gentle-dot/token          # lo crea el daemon, modo 0600
```

En el journal tiene que aparecer `Gentle Dot is running at http://127.0.0.1:4317/ (the
access key is in …/.gentle-dot/token)`: sin terminal, el daemon no escribe el token en
el log.

### Conectar la TUI

El daemon escucha en `127.0.0.1:4317` (`GENTLE_DOT_HOST` y `GENTLE_DOT_PORT` en la
unidad) y guarda su token en `~/.gentle-dot/token`, que son los valores por defecto de
la TUI:

```bash
cd host && python -m jarvis.tui
```

Para cambiar el puerto, un override (`systemctl --user edit gentle-dot.service`, con
`[Service]` y `Environment=GENTLE_DOT_PORT=4318`) y `python -m jarvis.tui --port 4318`.

Comprobación sin abrir la ventana (cliente y ventana sin pantalla contra el servicio
real; ver [Tests](#tests)):

```bash
cd host && JARVIS_REAL_DAEMON=1 python -m pytest tests/test_real_daemon.py -v -rs
```

Sin una cuenta de IA conectada en Gentle Dot el daemon rechaza cada mensaje con
`Connect an AI account first: open Accounts or type /login.`; la TUI muestra ese texto
tal cual (ver [What the window shows](#what-the-window-shows)) y no inicia sesión: la
cuenta se conecta en Gentle Dot.

### Desinstalar

```bash
host/systemd/install.sh --uninstall --dry-run
host/systemd/install.sh --uninstall
```

Detiene y deshabilita el servicio, borra la unidad y recarga systemd. No toca
`~/.gentle-dot` (datos, conversaciones y token) ni el checkout de Gentle Dot.

### Problemas frecuentes

| Síntoma | Causa y arreglo |
|---|---|
| `ExecStartPre` falla y el journal muestra el JSON de `--self-check` con `"ok": false` | Faltan archivos del motor: `pnpm install --frozen-lockfile` en el checkout |
| `status=203/EXEC` | La ruta de Node o del checkout cambió: volver a correr `install.sh` |
| `Start request repeated too quickly` | Más de 10 fallos en 10 minutos: ver el journal, arreglar y `systemctl --user reset-failed gentle-dot.service` |
| `EADDRINUSE` en el journal | Otro daemon (la aplicación de escritorio o un `pnpm dev`) ocupa el 4317: cerrarlo o cambiar el puerto |
| La TUI dice `No access token at …` | El daemon no arrancó nunca con ese `HOME`: ver `status` y el journal |
| La TUI dice `UNAUTHORIZED` | El token cambió: reiniciar la TUI |
| La TUI dice `SIGN IN` y `Connect an AI account first: …` | Gentle Dot no tiene una cuenta de IA: conectarla en Gentle Dot (Accounts o `/login` en su propia interfaz) y volver a escribir |
| El agente no puede usar `sudo` | Efecto buscado de `NoNewPrivileges=yes`; se puede quitar con un override |
| El servicio se detiene al cerrar sesión | Normal sin linger; ver Requisitos |

Sin la aplicación de escritorio, el proyecto original carga la configuración tal cual y
deja bloqueados los conectores que usan token (su README, «started without the app»);
tampoco hay control de la PC (D20).

### Pruebas

```bash
bash host/systemd/test_install.sh
```

Corre en un `HOME` y `XDG_CONFIG_HOME` temporales con `systemctl`, `node` e `id` falsos:
salida de `--dry-run` al instalar y desinstalar, idempotencia, negativa como root, con
Node viejo, con un checkout en otro commit o sin dependencias, y que no se escribe nada
fuera del directorio de unidades. Si `systemd-analyze` está instalado, además valida la
unidad con `systemd-analyze --user verify`. Corre en CI en el paso
`host · systemd unit`.

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
python -m jarvis.tui --thinking low           # wanted thinking level for typed prompts
python -m jarvis.tui --model openai/gpt-6.1-terra --thinking low
```

`--model provider/id` and `--thinking LEVEL` (`off`, `minimal`, `low`, `medium`,
`high`, `xhigh`, `max`; defaults `$JARVIS_TYPED_MODEL` and `$JARVIS_TYPED_THINKING`)
are optional. On connect the window asks the daemon for its models (`models_list`) and
sends `model_set` only when the running model or thinking level differs; without them
it never changes the model. The daemon saves a `model_set` as its default, for every
window, and rejects a level the model lacks with its own `thinking_unavailable`
message, shown as a notification.

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
| Jarvis card | the agent's state in large letters (`READY`, `THINKING`, `WORKING`, `NEEDS YOU`, `LISTENING`, `SPEAKING`, `STARTING`, `SIGN IN`, `ERROR`, `OFFLINE`), a spinner and a pulse on the card's edge while it works, what it is working on, and how many messages wait their turn |
| Voice device card | the device and its state in plain words (`Ready`, `Listening`, `Thinking`, `Speaking`, `Offline`…) with a hint, a level meter, the connection to this PC and the signal as bars (good, fair, weak, poor) |
| Thread | today's exchanges, newest at the bottom, on a timeline rail: time and `said ◎` / `typed ▸`, the user's words right-aligned in a bubble, the answer under `◆`, and what Jarvis did "on your PC" (`✓` done, `◐` running, `○` in the background, `✗` failed); a failed exchange says what to do next; the live one has an accent rail, a bold answer and `ctrl+a stops it`. `Today · N earlier above` counts the exchanges that do not fit |
| Sign-in notice | only while Gentle Dot has no AI account: the daemon refused a message with `no_account`, and its own text (`Connect an AI account first: open Accounts or type /login.`) stays on a `✗` row under the header, whole and unchanged, until a prompt runs again; the Jarvis card says `SIGN IN` |
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
with the agent and its PC actions: `model` is the running model and thinking level
(`GPT-6.1 Sol · medium`), `reply` the timing of the last prompt typed in this window
(`first text 2.6s · total 2.9s`, measured on the host from sending to the first
non-empty `message_delta` and to `message_done`; the daemon's frames carry no time),
and, while Gentle Dot has no AI account, a `needs` row with the daemon's message.
The user view shows no timing. Both views are fed by every event, so switching
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

## Timing the typed path: `python -m jarvis.bench`

```bash
cd host
python -m jarvis.bench --list                  # running model, its thinking levels, all models
python -m jarvis.bench                         # 3 x "Responde solo: ok": echo, first text, total, medians
python -m jarvis.bench -n 5 --prompt "Di hola"
python -m jarvis.bench --thinking low          # model_set on the running model first (PERSISTS)
python -m jarvis.bench --model openai/gpt-6-luna --thinking off
python -m jarvis.bench --model openai/gpt-6.1-sol --thinking medium --list   # switch back, no prompt
```

It connects like the window (`--url`, `--host`, `--port`, `--token-file`; the token is
read from the file and never printed) and sends prompts into the daemon's active
conversation. Per run it prints the seconds from sending to the echo (`user_message`),
to the first non-empty `message_delta` and to `message_done`, then the medians.
**`--model` / `--thinking` send `model_set`, which the daemon saves as its default**:
note the `model:` line first and pass it back to restore. Exit codes: 0 ok, 2 usage,
3 cannot connect (no daemon, token missing or refused), 4 the daemon answered an error
(printed as `code: message`, e.g. `thinking_unavailable`, `model_unavailable`,
`no_account`, or a failed reply), 5 timed out (`--timeout`, default 120 s; the prompt
is aborted).

## Tests

```bash
cd host
pip install -r requirements-dev.txt
python -m pytest tests -q
```

`tests/test_real_daemon.py` runs against the **real** daemon and is skipped unless
`JARVIS_REAL_DAEMON=1` (CI has no daemon). It connects the client, opens the window
headless and checks it reaches `daemon connected`, then types one short prompt: with
no AI account it expects the daemon's `no_account` message on screen; with one it
skips that check and stops the prompt. `JARVIS_DAEMON_URL` and
`JARVIS_DAEMON_TOKEN_FILE` override the defaults; the token is opened by the client and
never printed.

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
