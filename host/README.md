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
* `jarvis/tui.py` — Textual app: conversation, agent-state chip, action log, input,
  connection indicator.
* `requirements.txt` — `websockets` and `textual`, both `>=`.

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
unidad con `systemd-analyze --user verify`. Todavía no corre en CI.

## Running it

```bash
cd host
python -m jarvis.tui                          # ws://127.0.0.1:4317/ws, ~/.gentle-dot/token
python -m jarvis.tui --port 4318              # another port
python -m jarvis.tui --url ws://127.0.0.1:4317/ws
python -m jarvis.tui --token-file /path/to/token
python -m jarvis.tui --log-level debug        # client log on stderr (noisy under Textual)
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
| Detail line | the last connection message, for example a refused token or a protocol mismatch |
| Conversation | the user's messages and the assistant's answer, streamed as it arrives; the latest page of the chat's history on connect |
| Action log | `activity` frames (kind, what it did, running/done/failed), toasts, server errors, and the connection's own messages |
| Input | one line of text; sent with Enter |

What it deliberately does not do:

* **It never answers an `ask` approval.** Approvals are shown by the desktop app over
  its private channel, and this window only writes that one is waiting. A `pin` ask on
  a server is answered in the browser.
* No file attachments, no attachments upload, no conversation switching, no sign-in
  flows, and no paging back past the first history page.

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
