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
* `jarvis/voice.py` — push-to-talk (F4): records the microphone with `pw-record`
  (or `parecord` / `arecord`) and transcribes it with the local `whisper-server`;
  standard library only (see [Hablarle a Jarvis](#hablarle-a-jarvis)).
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
  1 min, como mucho 10 arranques en 10 minutos) y escribe en el journal. Arranca con la
  sesión gráfica (`graphical-session.target`) y se detiene al cerrarla. Sin secretos.
* `systemd/install.sh` — instala, verifica o quita la unidad. Solo escribe
  `~/.config/systemd/user/gentle-dot.service` (o bajo `$XDG_CONFIG_HOME`) y las reglas del
  agente (abajo), y solo llama a `systemctl --user`. Se niega a correr como root.
* `gentle-dot/AGENTS.md` — reglas del agente para el escritorio; ver
  [Reglas del agente](#reglas-del-agente-agentsmd).
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
* Una sesión gráfica que active `graphical-session.target` (niri, GNOME, KDE y los
  compositores que usan `uwsm` o `systemd --user` lo hacen; comprobar con
  `systemctl --user is-active graphical-session.target`). La unidad arranca **con la
  sesión** y no con el administrador de usuario al encender la PC: así hereda
  `WAYLAND_DISPLAY` y `DISPLAY`, que el agente necesita para abrir aplicaciones (etapa a
  de [D20](../docs/decisiones.md#d20--control-de-la-pc-por-el-agente-pista-s2-por-etapas)).
  Si arrancara antes que el compositor, las aplicaciones fallan con
  `Missing X server or $DISPLAY`.
* Sin sesión gráfica (un servidor, o para que arranque **sin iniciar sesión**):
  `--headless` instala la unidad en `default.target` y sin atarla a la sesión; el agente
  no podrá abrir ventanas. Para arrancar al encender la PC, opcional y fuera del script:
  `loginctl enable-linger "$USER"`.

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
hace nada. Correrlo **dentro de la sesión gráfica**, para que `try-restart` y `start`
dejen el daemon con `WAYLAND_DISPLAY`.

- Al actualizar desde una unidad anterior habilitada en `default.target` (o al pasar de
  `--headless` a la sesión, o al revés), `--enable` detecta el enlace viejo en
  `~/.config/systemd/user/default.target.wants/` y corre `systemctl --user reenable` +
  `start` en vez de `enable --now`, para no dejar el servicio arrancando también al
  encender la PC. Sin `--enable` solo avisa.

- `--allow-dirty`: acepta un checkout de Gentle Dot con cambios sin commitear o archivos sin seguimiento (desaconsejado). Sin ella, el instalador se niega y no escribe nada si `git -C <checkout> status --porcelain` no está vacío; `--check` también lo comprueba.
- Backup: antes de reemplazar una unidad distinta, el instalador guarda la anterior en `~/.config/systemd/user/gentle-dot.service.bak` e imprime la ruta. `--uninstall` la conserva.
- Límite de arranques: la unidad admite como mucho 10 arranques en 10 minutos (`StartLimitIntervalSec=10min`, `StartLimitBurst=10`); después queda en `failed` hasta `systemctl --user reset-failed gentle-dot.service`.
- Si `systemctl --user daemon-reload` falla, el instalador sale con error y deja `gentle-dot.service.reload-pending`; vuelve a ejecutarlo (o `--uninstall`) y reintentará la recarga en vez de responder "Sin cambios".

### Reglas del agente (`AGENTS.md`)

El agente del daemon lee `AGENTS.md` de su carpeta (`~/.gentle-dot/agent/`) cuando
arranca el motor. El de `host/gentle-dot/AGENTS.md` le dice que abra las aplicaciones
gráficas **en segundo plano** (`setsid -f <programa> >/dev/null 2>&1` o
`gtk-launch <id>`) y que confirme con `pgrep -f <programa>`: un `brave` en primer plano
no termina nunca, la herramienta de shell corta a los 10 s y el agente responde que no
pudo aunque la app se abrió (L13 de `odd/tasks/s2-round2.md`).

- `install.sh` lo copia como `~/.gentle-dot/agent/AGENTS.md` (0644). Otra carpeta:
  `--agent-home DIR` o `GENTLE_DOT_AGENT_HOME=DIR` (ruta absoluta; tiene que ser la que
  usa el daemon). Si ya está igual, dice "Sin cambios"; si hay uno distinto, guarda el
  anterior en `AGENTS.md.bak` e imprime la ruta. `--dry-run` solo lo anuncia y
  `--check` dice si está al día, falta o está desactualizado.
- Como se lee al arrancar el motor: con `--enable`, un cambio en las reglas hace
  `systemctl --user try-restart gentle-dot.service`; sin `--enable` el script imprime ese
  comando para que lo corras.
- Para editarlo: cambiar `host/gentle-dot/AGENTS.md` en el repositorio y volver a correr
  `install.sh --enable` (así queda versionado). Si se edita directamente
  `~/.gentle-dot/agent/AGENTS.md`, reiniciar el servicio; la próxima instalación lo
  reemplaza y deja tu versión en `AGENTS.md.bak`.
- `--uninstall` no lo borra (puede tener cambios tuyos y sin el servicio no hace nada);
  dice dónde quedó.

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
tal cual (ver [What the window shows](#what-the-window-shows)). La cuenta se conecta
desde la misma TUI escribiendo `/login` (ver [Signing in with /login](#signing-in-with-login)).

### Desinstalar

```bash
host/systemd/install.sh --uninstall --dry-run
host/systemd/install.sh --uninstall
```

Detiene y deshabilita el servicio, borra la unidad y recarga systemd. No toca
`~/.gentle-dot` (datos, conversaciones, token y `agent/AGENTS.md`) ni el checkout de
Gentle Dot.

### Problemas frecuentes

| Síntoma | Causa y arreglo |
|---|---|
| `ExecStartPre` falla y el journal muestra el JSON de `--self-check` con `"ok": false` | Faltan archivos del motor: `pnpm install --frozen-lockfile` en el checkout |
| `status=203/EXEC` | La ruta de Node o del checkout cambió: volver a correr `install.sh` |
| `Start request repeated too quickly` | Más de 10 fallos en 10 minutos: ver el journal, arreglar y `systemctl --user reset-failed gentle-dot.service` |
| `EADDRINUSE` en el journal | Otro daemon (la aplicación de escritorio o un `pnpm dev`) ocupa el 4317: cerrarlo o cambiar el puerto |
| La TUI dice `No access token at …` | El daemon no arrancó nunca con ese `HOME`: ver `status` y el journal |
| La TUI dice `UNAUTHORIZED` | El token cambió: reiniciar la TUI |
| La TUI dice `SIGN IN` y `Connect an AI account first: …` | Gentle Dot no tiene una cuenta de IA: escribir `/login` en la TUI, elegir la cuenta y volver a escribir |
| `/login` dice `Gentle Dot is not connected: /login needs it.` | El daemon no está conectado: ver `status` y esperar `daemon connected` |
| `/login` con Anthropic muestra `Signing in with a Claude subscription is not supported. …` | El daemon solo acepta Anthropic con API key: elegir `API key` |
| `/login` muestra `Another sign-in is in progress. …` | Otra ventana (o la app de escritorio) tiene un inicio de sesión abierto: terminarlo o cancelarlo allí |
| El agente no puede usar `sudo` | Efecto buscado de `NoNewPrivileges=yes`; se puede quitar con un override |
| El servicio se detiene al cerrar sesión | Normal: la unidad es `PartOf=graphical-session.target`; ver Requisitos (`--headless` si se quiere sin sesión) |
| La app no abre / `Missing X server or $DISPLAY` en el journal | El servicio arrancó antes de la sesión gráfica y no tiene `WAYLAND_DISPLAY` ni `DISPLAY`; con esta unidad arranca con la sesión (volver a correr `install.sh --enable` si la instalada es anterior). Mientras tanto: `systemctl --user restart gentle-dot` dentro de la sesión |
| La app se abre pero Jarvis dice que no pudo (`Command timed out after 10 seconds`) | Falta la regla de lanzar en segundo plano: el agente corrió la app en primer plano. `install.sh --enable` instala `~/.gentle-dot/agent/AGENTS.md` y reinicia el servicio (`install.sh --check` dice si está al día) |
| El servicio no arranca al iniciar sesión | El compositor no activa `graphical-session.target` (`systemctl --user is-active graphical-session.target`): usar `--headless` o arrancarlo desde el compositor |

Sin la aplicación de escritorio, el proyecto original carga la configuración tal cual y
deja bloqueados los conectores que usan token (su README, «started without the app»);
tampoco hay control de la PC (D20).

### Pruebas

```bash
bash host/systemd/test_install.sh
```

Corre en un `HOME` y `XDG_CONFIG_HOME` temporales con `systemctl`, `node` e `id` falsos:
salida de `--dry-run` al instalar y desinstalar, idempotencia, negativa como root, con
Node viejo, con un checkout en otro commit o sin dependencias, que la unidad va con la
sesión gráfica (y `--headless` con `default.target`), que `--enable` vuelve a habilitar
una unidad que quedó en el otro target, que las reglas del agente se instalan (con backup
de una versión distinta, `--agent-home`, sin borrarlas al desinstalar) y que no se
escribe nada fuera del directorio de unidades y de la carpeta del agente. Si `systemd-analyze` está instalado, además valida la
unidad con `systemd-analyze --user verify`. Corre en CI en el paso
`host · systemd unit`.

## Puente de Pi en lugar de Gentle Dot

Gentle Dot sigue siendo **lo predeterminado**: `install.sh` sin opciones nuevas instala
exactamente lo mismo que antes. Una máquina puede elegir, en cambio, el puente de Pi
([D24](../docs/decisiones.md#d24--puente-de-pi-con-el-protocolo-de-gentle-dot)):
`jarvis/pibridge` (`python -m jarvis.pibridge`) es un daemon que habla el mismo
protocolo v1 de Gentle Dot, en el mismo `127.0.0.1:4317` y con el mismo token
(`~/.gentle-dot/token`), y por debajo maneja un proceso `pi --mode rpc` con la extensión
de control de la PC y el `AGENTS.md` de Jarvis. La TUI, `python -m jarvis.bench` y el
backend no cambian. Medido el 2026-11-25 con el mismo modelo: las preguntas bajan de
6,6-7,4 s a 1,9-2,5 s y subir o bajar el volumen de 14-16 s a 4,2-4,8 s.

* `systemd/jarvis-pibridge.service.in` — plantilla de la unidad
  `jarvis-pibridge.service`. En el aislamiento es igual a `gentle-dot.service` (sin
  pantalla, sin bus de sesión, home de solo lectura, el mismo envoltorio Landlock) salvo
  una línea: `ReadWritePaths=` suma la **carpeta propia del Pi del puente** y la **carpeta
  propia de Claude Code del puente** (ver abajo). Fuera del aislamiento cambia lo propio de
  otro daemon: el `ExecStart=`, las variables `JARVIS_PI_*`, `PI_CODING_AGENT_DIR`,
  `CLAUDE_CONFIG_DIR` y `PATH`, y `Conflicts=`. Corre con el entorno de
  Python que `install.sh` ya arma para la TUI (`~/.local/share/jarvis/tui/`), así que el
  puente es la copia instalada de `host/jarvis`, no el checkout.
* Lleva `Conflicts=gentle-dot.service`: arrancar una detiene la otra, en los dos
  sentidos. Nunca corren juntas en el mismo puerto.

**Carpeta propia de Pi.** Pi necesita escribir en su carpeta de agente (configuración,
`trust.json` y su `.lock`, cuentas, caché de modelos; sin eso no arranca). El Pi del
puente no usa la tuya (`~/.pi/agent`) sino una suya, fija,
`~/.local/state/jarvis/pi-agent` (0700, y 0700 también las carpetas de arriba que cree
el script), que la unidad le pasa en `PI_CODING_AGENT_DIR` y es lo
único de Pi donde el servicio puede escribir. `~/.pi` queda de **solo lectura** para el
puente: si pudiera escribirla, el agente podría dejar ahí una extensión o tocar un
paquete y tu `pi` de la terminal, que corre sin aislamiento, la ejecutaría al arrancar.
La carpeta tampoco está dentro de `~/.gentle-dot` ni de `~/Jarvis`, donde también escribe
`gentle-dot.service`: su agente podría cambiarla por un enlace a `~/.pi`. No se puede
cambiar de lugar, y la unidad la nombra tal cual. Antes de escribir nada, el script se
niega a instalar si algo de esa ruta bajo `~` es un enlace simbólico (`~/.local`,
`~/.local/state`, `~/.local/state/jarvis` o la propia carpeta; dice la ruta y adónde
apunta), si existe pero no es una carpeta, o si ya existe con permisos para el grupo u
otros (dice el `chmod 700` que la arregla). También se niega si `HOME` no es tu carpeta
personal según `getent passwd` (systemd usa esa en las demás rutas de la unidad; dice con
qué `HOME` volver a correrlo), si el grupo u otros pueden escribir en `~/.local`,
`~/.local/state` o `~/.local/state/jarvis` (dice el `chmod go-w` que lo arregla), o si la
ruta tiene caracteres que una unidad no admite (espacios, `%`, comillas…). Si quedó `~/.gentle-dot/pi-agent` de una versión anterior
del script, no la mueve ni la borra: avisa que ya no se usa (no corras Pi en ella). El
home sigue visible, así que las extensiones extra de `~/.pi/agent/npm/...` que nombres en
`JARVIS_PI_EXTENSIONS` se siguen cargando (solo lectura).

**Iniciar sesión.** La carpeta nace vacía: `install.sh` no copia ni lee credenciales.
Para iniciar sesión hay que correr Pi **fuera del aislamiento** en esa carpeta, y Pi
ejecuta lo que encuentra ahí: instala los paquetes que liste `settings.json` y corre su
`npmCommand`, ejecuta las entradas `!comando` de `auth.json` o `models.json` al resolver
los proveedores, y prefiere las herramientas de `bin/` a las del sistema. Una carpeta en
la que el agente ya escribió no es segura para eso, con o sin `-ne -ns -np -nc
--no-themes` (esas opciones solo evitan cargar extensiones, skills, prompts, archivos de
contexto y temas; no protegen de lo anterior). La única protección es la regla: **Pi
corre fuera del aislamiento solo en una carpeta vacía, que el agente nunca escribió.**
El script decide por la lista de la carpeta (si la creó en esta corrida o no tiene
entradas; no abre nada de adentro) y según eso imprime uno de los dos procedimientos, con
tus rutas.

*Primera vez* (carpeta nueva o vacía), antes del primer arranque del servicio, así que
conviene instalar sin `--enable`:

```bash
host/systemd/install.sh --pi-bridge --node "$(mise where node@24)/bin/node"
cd ~ && env PI_CODING_AGENT_DIR=$HOME/.local/state/jarvis/pi-agent "$(command -v pi)" -ne -ns -np -nc --no-themes
# dentro de Pi: /login, elegir el proveedor (el del modelo, por defecto openai) y salir
systemctl --user enable --now jarvis-input.service jarvis-pibridge.service
```

Los dos inicios de sesión (Pi y Claude Code) empiezan con `cd ~ &&`: corridos desde
`~/Jarvis` o `~/.gentle-dot` podrían cargar archivos de proyecto que el agente puede escribir.

Con `--enable` sobre una carpeta de Pi vacía (y la de Claude Code con contenido; si está
vacía, ver abajo) el script habilita y arranca el servicio igual (Pi ya arrancó en esa
carpeta) y avisa que es inofensivo: sin sesión no corre ningún modelo, así que ninguna
herramienta puede actuar. Inicia sesión con la línea de arriba y luego
`systemctl --user restart jarvis-pibridge.service`.

*Volver a iniciar sesión* (otra cuenta, sesión vencida; la carpeta ya tiene contenido):
nunca corras Pi en la carpeta vieja. El script imprime estos pasos:

```bash
systemctl --user stop jarvis-pibridge.service
mv ~/.local/state/jarvis/pi-agent ~/.local/state/jarvis/pi-agent.old-AAAAMMDD-HHMMSS  # el script da el nombre con la hora
host/systemd/install.sh --pi-bridge --node "$(mise where node@24)/bin/node"  # sin --enable: crea una carpeta nueva, vacía, 0700
cd ~ && env PI_CODING_AGENT_DIR=$HOME/.local/state/jarvis/pi-agent "$(command -v pi)" -ne -ns -np -nc --no-themes  # /login y salir
systemctl --user start jarvis-pibridge.service
```

La carpeta movida queda como está (no corras Pi en ella; bórrala cuando quieras). Si el
proveedor lo trae una extensión (por ejemplo `pi-claude-bridge`), agrégala con
`-e <ruta>`. La cuenta queda en la carpeta y se conserva al desinstalar.

**Carpeta propia de Claude Code.** Con los modelos de Claude por suscripción (ver *Modelos
de Claude por suscripción*, abajo) la extensión `pi-claude-bridge` arranca Claude Code
(con el Claude Agent SDK), y Claude Code escribe en su carpeta de configuración: las
sesiones en `projects/`, `.claude.json` y la renovación del token. El puente no le da la
tuya (`~/.claude` y `~/.claude.json` siguen de **solo lectura**: tu `claude` de la
terminal, sin aislamiento, ejecuta los hooks y la configuración que encuentra ahí) sino
una suya, fija, `~/.local/state/jarvis/claude-config` (0700), que la unidad le pasa en
`CLAUDE_CONFIG_DIR` y es lo único de Claude donde el servicio puede escribir. El script la
crea siempre con `--pi-bridge`, aunque uses otro modelo, y le aplica exactamente los mismos
rechazos que a la de Pi (el mismo código): enlace simbólico en la ruta, existe y no es una
carpeta, permisos para el grupo u otros en ella o escritura en las de arriba, `HOME`
distinto de tu carpeta personal, caracteres que una unidad no admite. También rechaza un
`XDG_CONFIG_HOME`, `--bin-dir` o `--agent-home` dentro de cualquiera de las dos (los
crearía 755 y con contenido antes que ellas). Un enlace simbólico en `~/.local`,
`~/.local/state` o `~/.local/state/jarvis` se rechaza nombrando las dos carpetas; si una
de esas no es una carpeta o el grupo u otros pueden escribir en ella, el rechazo nombra esa
de arriba. Antes de escribir
nada. No hay opción ni variable para moverla. El envoltorio Landlock no limita archivos
(solo sockets abstractos y señales), así que no hace falta abrirle nada.

La regla para iniciar sesión es la misma que con Pi: Claude Code corre fuera del
aislamiento solo en esa carpeta **vacía, que el agente nunca escribió**, porque ejecutaría
lo que el agente hubiera dejado (hooks, `statusLine` y `apiKeyHelper` de `settings.json`,
servidores MCP de `.claude.json`, plugins, agentes y comandos). El script lo imprime antes
que los pasos de Pi:

```bash
# carpeta nueva o vacía, antes del primer arranque del servicio con ella
cd ~ && env CLAUDE_CONFIG_DIR=$HOME/.local/state/jarvis/claude-config claude
# dentro de Claude Code: /login con tu suscripción y salir
```

Con `--enable` sobre esa carpeta vacía el script no habilita ni arranca el puente (ni
detiene Gentle Dot; solo el ayudante): con el servicio en marcha, un Pi con sesión podría
escribir en ella antes de que corras `claude`. Imprime `systemctl --user stop
jarvis-pibridge.service`, la línea de arriba y que vuelvas a correr el script con
`--enable`; si no vas a usar los modelos de Claude, el comando para arrancarlo igual. Además
deshabilita y detiene el puente (`systemctl --user disable --now`), haya cambios o no, y lo
dice si estaba corriendo, arrancando o habilitado: corriendo, o al arrancar con la próxima
sesión, el agente podría escribir en la carpeta vacía, y reiniciarlo la abriría a la unidad
nueva. Queda así hasta el siguiente `--enable`, después del inicio de sesión, que lo
habilita y arranca con la unidad, las reglas y la extensión actuales. Es
lo que pasa también al actualizar una instalación anterior del puente, cuyo Pi ya tiene
sesión. Si ya tiene contenido, nunca corras `claude` en ella; el script da el mismo
procedimiento que para Pi:

```bash
systemctl --user stop jarvis-pibridge.service
mv ~/.local/state/jarvis/claude-config ~/.local/state/jarvis/claude-config.old-AAAAMMDD-HHMMSS
host/systemd/install.sh --pi-bridge --node "$(mise where node@24)/bin/node"  # sin --enable: carpeta nueva, vacía, 0700
cd ~ && env CLAUDE_CONFIG_DIR=$HOME/.local/state/jarvis/claude-config claude  # /login y salir
systemctl --user start jarvis-pibridge.service
```

Requisitos: `pi` instalado, el **binario real** de Node 24+, como para Gentle Dot (un
shim de mise no sirve), y la sesión de arriba. No hace falta el checkout de Gentle Dot.

### Pasar al puente

```bash
host/systemd/install.sh --pi-bridge --node "$(mise where node@24)/bin/node" --check
host/systemd/install.sh --pi-bridge --node "$(mise where node@24)/bin/node" --enable
```

`--pi /ruta/a/pi` si `pi` no está en el `PATH` (por defecto, `command -v pi`).
`--gentle-dot-dir`, `--allow-unpinned` y `--allow-dirty` son solo de Gentle Dot y se
rechazan junto con `--pi-bridge`. Instala lo mismo que la opción por defecto (reglas,
extensión, parada, ayudante, TUI) salvo la unidad: escribe
`~/.config/systemd/user/jarvis-pibridge.service` (la anterior, si era distinta, en `.bak`)
y crea la carpeta propia de Pi (0700) si falta; `--check` dice si esa carpeta existe (no
mira si tiene una sesión). Con `--enable` (salvo con la carpeta de Claude Code vacía, ver
arriba), si `gentle-dot.service` está instalado,
primero corre `systemctl --user disable --now gentle-dot.service` y después habilita y
arranca el puente; la unidad de Gentle Dot queda en su lugar. Si ese `disable --now` o el
arranque del puente fallan, puede no quedar ninguno de los dos corriendo: el script dice
qué paso falló (el `disable` de Gentle Dot o el `enable`, `start` o `restart` del puente) y da los comandos para ver el error y volver a Gentle Dot. Sin `--enable` no habilita, arranca ni detiene nada:
solo corre `systemctl --user daemon-reload` si la unidad cambió, y avisa qué correr.

### Volver a Gentle Dot

```bash
host/systemd/install.sh --gentle-dot-dir ~/ruta/gentle-dot \
  --node "$(mise where node@24)/bin/node" --enable
```

Es la instalación de siempre: con `--enable`, si el puente está instalado, primero corre
`systemctl --user disable --now jarvis-pibridge.service` y después habilita Gentle Dot.
`--uninstall` quita las dos unidades (y el ayudante); no toca `~/.pi` ni `~/.gentle-dot`
(tampoco las carpetas propias de Pi y de Claude Code, con sus sesiones).

### Elegir el modelo

Por defecto el puente usa `openai/gpt-6.1-sol` con razonamiento `low`. Se cambia sin
editar la unidad, con un override (`systemctl --user edit jarvis-pibridge.service`):

```ini
[Service]
Environment=JARVIS_PI_MODEL=openai/gpt-6.1-terra
Environment=JARVIS_PI_THINKING=low
# Extensiones extra de Pi, separadas por ":"; por ejemplo, la de los modelos de Claude
# por suscripción:
Environment=JARVIS_PI_EXTENSIONS=%h/.pi/agent/npm/node_modules/pi-claude-bridge
```

y `systemctl --user restart jarvis-pibridge.service`. Las opciones de
`python -m jarvis.pibridge --help` se pueden poner así como `JARVIS_PI_<NOMBRE>`
(`--record-timeout` es `JARVIS_PI_RECORD_TIMEOUT`), con dos excepciones: `--pi` es
`JARVIS_PI_EXECUTABLE` y `--extension` es `JARVIS_PI_EXTENSIONS` (varias, separadas por
`:`). El modelo que se elige desde la TUI vale hasta que el servicio se reinicia.

### Modelos de Claude por suscripción

Sin cuenta de OpenAI ni API key, el puente puede usar tu suscripción de Claude con la
extensión `pi-claude-bridge` (instalada en tu Pi, en
`~/.pi/agent/npm/node_modules/pi-claude-bridge`; el servicio la lee, no la escribe). El
repo no cambia de modelo por defecto: se elige con el mismo override
(`systemctl --user edit jarvis-pibridge.service`):

```ini
[Service]
Environment=JARVIS_PI_MODEL=claude-bridge/claude-sonnet-5-5
Environment=JARVIS_PI_THINKING=low
Environment=JARVIS_PI_EXTENSIONS=%h/.pi/agent/npm/node_modules/pi-claude-bridge
```

Antes, con el servicio detenido (`systemctl --user stop jarvis-pibridge.service`), inicia
sesión en Claude Code una vez en su carpeta propia (ver *Carpeta propia de Claude Code*,
arriba): `cd ~ && env CLAUDE_CONFIG_DIR=$HOME/.local/state/jarvis/claude-config claude`,
`/login` y salir. Después, `systemctl --user start jarvis-pibridge.service`.

### Ver que está corriendo

```bash
systemctl --user status jarvis-pibridge.service
systemctl --user is-active gentle-dot.service      # tiene que decir inactive
journalctl --user -u jarvis-pibridge -f
```

Al arrancar, el journal muestra `token file …/.gentle-dot/token, model
openai/gpt-6.1-sol:low` (nunca el token). Después, la TUI (`jarvis`) se conecta igual que
a Gentle Dot. Si el journal dice `cannot listen on 127.0.0.1:4317`, otro daemon ocupa el
puerto: ver que `gentle-dot.service` esté detenido o volver a correr el script con
`--pi-bridge --enable`.

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

When the backend flags a slow speech-to-text stage (`warning` on `turn_stage`, sent
when whisper-server seems to run on CPU: ~15 s per turn against ~0.35 s on a Vulkan
GPU), the window shows it once as a warning notification and writes it to the log.

To see it without a board, start the backend and run the simulator from the repo root:

```bash
python tools/sim_device.py --url http://127.0.0.1:8000
```

Keys (all listed in the footer): **Enter** sends the text in the input row, **Ctrl+A**
stops the answer that is running, **Ctrl+Q** quits, **F2** switches between the user
view and the developer view, **F3** switches between dark and light mode, **F4**
starts recording what you say and, pressed again, sends it (see
[Hablarle a Jarvis](#hablarle-a-jarvis)); while it records, **Esc** drops it. **F5**
turns PC control on or off (see [Control de la PC](#control-de-la-pc)); while a
confirmation card is up, **y** allows, **n** or **Esc** denies. While the
`/login` panel is open the footer shows its own keys: **Enter** chooses or answers,
**Esc** goes back, closes the panel or cancels the sign-in that is running, and
**Ctrl+Y** copies the sign-in address (when one is shown) to the clipboard.

## Signing in with /login

Type `/login` and press Enter: the window does not send it to the assistant, it asks
the daemon for its accounts (`auth_list`) and opens the **Sign in** card above the
input, in both views and in the active theme. Nothing opens a browser by itself.

1. The card lists every account the daemon offers, numbered, with `connected` /
   `not connected` and the ways to sign in (`subscription (<plan>)`, `API key`). Type
   the number and press Enter.
2. With two ways, choose one by number (Esc goes back to the list).
3. The daemon drives the rest and the card shows each step: an address to open (whole,
   on its own rows, so it can be selected; Ctrl+Y copies it), a device code with the
   address to type it at and when it expires, progress and info lines.
4. When the daemon asks something, answer in the same input: an API key (`secret`) is
   hidden as you type (`•`); a choice (`select`) is a numbered list; a text or a pasted
   code (`text`, `manual_code`) shows as typed.
5. The card ends with `✓ Signed in to <account>.` or the daemon's own message
   (`Sign-in cancelled.`, a refusal, `That sign-in is no longer active.`…); the sign-in
   notice goes away on success. Esc closes the card; a second Esc closes it even while
   the daemon has not confirmed a cancel. If the daemon link drops, the sign-in ends
   (`The connection to Gentle Dot dropped: the sign-in ended.`): start it again.

Whenever a prompt closes (answered, Esc, the daemon ends it, the link drops, the card
closes) the input is emptied before it stops hiding what was typed, so a half-typed API
key is never shown and never sent as a message. While a secret is hidden, copy and cut
(Ctrl+C, Ctrl+X) and Ctrl+Y do nothing. A flow left with Esc before the daemon named it
is cancelled as soon as the daemon does, so the next `/login` is not refused as busy.

Anthropic is offered with an API key only: the daemon refuses a Claude subscription
sign-in, and the card shows its message as is. What you type in the card goes straight
to the daemon: it is not shown (a secret), kept in the window's state, written to the
history or logged, even with `--log-level debug` (the WebSocket frame log never goes
below `info`).

Without a daemon the client retries with backoff (500 ms, 1 s, 2 s, 5 s) with no
traceback and no freeze. The user view says that Gentle Dot is not running (or has no
access token yet) and keeps showing the voice side; the developer view shows
`○ daemon disconnected` in its top border.

## Hablarle a Jarvis

**F4** graba el micrófono predeterminado y, al volver a pulsarla, transcribe lo dicho con
el `whisper-server` local y lo manda a Gentle Dot igual que un mensaje escrito (se mide
igual y se ve como `said ◎` en la vista de usuario y con `◎` junto a `you` en la vista
de desarrollador). Mientras graba, una fila sobre el campo de texto muestra
`● recording 3.2s  ·  f4 to send  ·  esc cancels`; luego `◐ transcribing…`. **Esc**
cancela sin mandar nada. La grabación se corta sola a los 60 s y se envía.

* Audio: WAV PCM 16 kHz mono 16 bits (D6), con
  `pw-record --rate 16000 --channels 1 --format s16 <archivo>`; si falta, `parecord`
  o `arecord`. Sin ninguno: `No recorder found: install pw-record (PipeWire), parecord
  or arecord.`
* El archivo va a una carpeta temporal privada (`0700`) y se borra al terminar, se
  mande o no.
* Transcripción: `POST` multipart (`file`, `response_format=json`, `language=es`) a
  `JARVIS_STT_URL` (por defecto `http://127.0.0.1:8080/inference`), con 30 s de límite.
* Si no se oyó nada: `Nothing was heard: nothing was sent.` Si el servidor no responde
  o contesta otra cosa que 200, la notificación dice la causa exacta (por ejemplo
  `Can't reach the speech server at http://127.0.0.1:8080/inference: [Errno 111]
  Connection refused. Nothing was sent.`) y no se manda nada.
* Con el panel `/login` abierto F4 no graba, y abrir `/login` mientras se graba descarta
  la grabación: un secreto nunca se graba ni se manda.

Requiere `whisper-server` (whisper.cpp) escuchando en local, con el modelo cargado una
vez. Si está instalado como servicio de usuario (tarea T6):

```bash
systemctl --user start whisper-server
```

O a mano:

```bash
whisper-server -m ~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin \
  --host 127.0.0.1 --port 8080
JARVIS_STT_URL=http://127.0.0.1:8080/inference python -m jarvis.tui
```

Prueba real opcional (no corre por defecto), que transcribe un WAV con el mismo cliente
de la ventana:

```bash
JARVIS_REAL_WHISPER=1 JARVIS_WHISPER_WAV=~/jarvis-prueba.wav \
  python -m pytest tests/test_voice.py -k real -s
```

## Control de la PC

Jarvis puede escribir, pulsar teclas y sacar capturas en tu sesión, pero **solo con permiso
y por tiempo limitado**. El control está apagado por defecto.

**Arquitectura: el ayudante `jarvis-input`.** Un servicio de usuario aparte
(`jarvis-input.service`, en `graphical-session.target`) es el **único** proceso con acceso a
la pantalla: solo él corre `wtype`, `grim`, `niri msg`, `gtk-launch` y `xdg-open`, y guarda
el permiso **en memoria** (reiniciarlo = permiso revocado). El daemon de Gentle Dot corre
aislado, sin Wayland ni acceso a `jarvis-control/`: el agente le pide al ayudante que abra
apps o teclee por `$XDG_RUNTIME_DIR/jarvis/agent.sock`, y el ayudante aplica la lista de
bloqueo y las confirmaciones según la acción (D23). Lo instala
`host/systemd/install.sh`; **`jarvis-input` tiene que estar corriendo** para usar el control
(`systemctl --user status jarvis-input`).

Música y volumen (`pc_media`: pausa, siguiente, anterior, subir, bajar y silenciar) no
necesitan `/control on` ni una ventana enfocada: son acciones del catálogo (D8) y el ayudante
corre `playerctl` y `wpctl` sin teclas. Requieren esos dos programas instalados.

La TUI habla con el ayudante por `$XDG_RUNTIME_DIR/jarvis-control/control.sock` (JSON por
líneas; solo la TUI y `jarvis-stop`). Ya no escribe ningún archivo de permiso.

* `/control on` (o **F5**) manda `{"op":"grant","minutes":30}` (máximo 30) y el ayudante
  contesta `{"ok":true,"expires_at":"…Z"}`. `/control off` (o **F5** otra vez) manda
  `{"op":"revoke"}`. `/control status` (o `/control` solo) pregunta `{"op":"status"}` y dice
  cuánto falta. Nada de `/control` se manda al agente.
* La TUI mantiene una conexión `{"op":"subscribe"}` abierta y recibe
  `{"event":"state","enabled":…,"expires_at":…}` y los pedidos de confirmación. Si el
  ayudante se reinicia, se reconecta sola con espera creciente (0,5 s a 5 s).
* Mientras está encendido, las dos vistas muestran sobre el campo de texto
  `◉ control on · 29:12 left · /control off`, con la cuenta regresiva hasta el `expires_at`
  del ayudante, que es quien manda. Al llegar a cero la barra se va y avisa
  `control expired` (lo vence el ayudante; la TUI no revoca por su cuenta). Si se revoca por
  otro lado (la parada de emergencia), avisa `Control off: jarvis-input revoked it …`.
* **Si el ayudante no está**, `/control` avisa `jarvis-input is not running: …` con cómo
  arrancarlo, y la barra muestra `◌ control unavailable · jarvis-input is not running` hasta
  que vuelve. Si se cae con el control encendido, el control queda apagado.
* **Al cerrar la TUI se manda `revoke`**: el control nunca dura más que la ventana que lo
  dio. Para volver a usarlo, abrirla y escribir `/control on` de nuevo.
* Sin `XDG_RUNTIME_DIR` (fuera de la sesión gráfica) la TUI se niega:
  `XDG_RUNTIME_DIR is not set: PC control needs your session's private runtime directory. …`.

**Parada de emergencia: `Alt+Shift+Esc`.** Ejecuta `jarvis-stop` (`host/bin/`), que manda
`revoke` por `control.sock` y `{"type":"abort"}` al daemon para cortar la tarea. El atajo lo
agrega cada uno en niri, en `~/.config/niri/binds.kdl`, por ejemplo
`Alt+Shift+Escape { spawn "jarvis-stop"; }`.

**Confirmaciones.** Antes de una acción irreversible, según la acción y nunca según las
palabras (D23: enviar en un chat o correo, borrar todo el contenido, cerrar una ventana o
app, también cuando el borrado se reparte entre dos llamadas seguidas a la misma ventana),
el ayudante manda por la suscripción
`{"event":"confirm","id":…,"title":…,"message":…,"timeout_s":30}`. La TUI lo muestra como
una tarjeta destacada sobre el campo de texto, en las dos vistas, y contesta
`{"op":"answer","id":…,"allow":true|false}` por la misma conexión. Las confirmaciones que
otras extensiones piden por el daemon (`{"type":"ask","ask":{"method":"confirm",…}}`) usan
la misma tarjeta y se contestan con `{"type":"ui_response","requestId":…,"confirmed":…}`.

* **y** permite; **n** o **Esc** niegan. **Enter no contesta** y nada más contesta: solo una
  `y` explícita permite. Las teclas de los primeros 0,4 s no cuentan, para que una `y` que se
  estaba escribiendo no se tome como respuesta.
* Una tarjeta a la vez (`+1 waiting` si hay más). Si se acaba el tiempo (`denied in 27s` en
  la tarjeta), se corta la conexión de quien pregunta (el ayudante o el daemon) u otra
  ventana contesta (`ask_resolved`), la tarjeta se va y cuenta como **negado**. Sin
  suscriptor o sin respuesta, el ayudante niega. Nunca confirma sola.
* Mientras la tarjeta está, el campo de texto queda desactivado, el panel `/login` se
  oculta y F4 no graba; si estaba grabando, la grabación se descarta.
* Otros pedidos del daemon (`select`, `input`, `editor`, `app`, `pin`) no se contestan aquí:
  la TUI avisa `Gentle Dot is asking: …. Answer this in Gentle Dot.` y sigue.

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
| Sign-in notice | only while Gentle Dot has no AI account: the daemon refused a message with `no_account`, and its own text (`Connect an AI account first: open Accounts or type /login.`) stays on a `✗` row under the header, whole and unchanged, until a prompt runs again or `/login` succeeds (when the text does not mention `/login`, a `Type /login to connect one.` row follows it); the Jarvis card says `SIGN IN` |
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
reply under each; a summary per lane; the typed conversation (`◆ jarvis`), where each
reply to a prompt typed in this window ends its first line with its total, dim
(`2.9s`; replies from other windows or from before connecting show none); and a card
with the agent and its PC actions: `model` is the running model and thinking level
(`GPT-6.1 Sol · medium`), `reply` the timing of the last prompt typed in this window
(`first text 2.6s · total 2.9s`, measured on the host from sending to the first
non-empty text of any of its messages and to the agent going back to `idle`, so a
tool-using reply, an empty tool-call message and then the text, is timed whole; until
`idle` the total is the last `message_done`, which stays if no `idle` comes, a stop
or error ends it, or the next prompt or a dropped link cuts it; the daemon's frames
carry no time),
and, while Gentle Dot has no AI account, a `needs` row with the daemon's message.
The user view shows no timing. Both views are fed by every event, so switching
always shows current data. Two gaps of contract C2 show on screen: an `action` has no
`turn_id`, so it is listed under the open turn of the same device; the lane of a live
turn is only known when it ends (`lane —` until then). The backend does not emit
`turn_stage` or `turn_error` yet, so stage figures and heard text stay empty against
it today (the user view shows `you spoke` in the bubble).

What it deliberately does not do:

* **It answers only `confirm` asks** (see [Control de la PC](#control-de-la-pc)), and only
  with an explicit **y** or a denial. Every other `ask` (approvals of the desktop app,
  `pin` on a server, `select`, `input`, `editor`) is answered in Gentle Dot; this window
  only writes that one is waiting.
* No file attachments, no attachments upload, no conversation switching, no sign-out
  (`/login` signs in only), and no paging back past the first history page.

## Turn log: every stage of a whole turn

Each prompt typed or said (f4) in the window is timed from sending to the agent going
back to `idle`, stage by stage, and appended as **one JSON line** to
`$XDG_STATE_HOME/jarvis/turns.jsonl` (default `~/.local/state/jarvis/turns.jsonl`, mode
`0600`). `JARVIS_TURN_LOG=/path/file.jsonl` moves it; `JARVIS_TURN_LOG=off` turns it off.
A write that fails is logged once as a warning and never stops the turn. The developer
view's `turn` row shows the last one: `2 calls 3.8s · 1 tool · wait 3.0s` (model calls
and their time, tools, time a person took on confirmation cards).

```json
{"v": 1, "at": "2026-11-23T20:30:00.123+00:00", "source": "tui", "spoken": false,
 "chars": 12, "stt_s": null, "echo_s": 0.1, "first_text_s": 7.0, "total_s": 7.6,
 "ended_by": "idle", "error": null, "stopped": false, "model_calls_n": 2, "tools_n": 1,
 "model_s": 3.8, "tool_s": 3.5, "human_wait_s": 3.0,
 "model_calls": [{"start_s": 0.1, "end_s": 2.0, "first_text_s": null, "error": null}, …],
 "tools": [{"kind": "open_app", "status": "done", "start_s": 2.1, "end_s": 5.6, "call": 0}],
 "waits": [{"start_s": 2.2, "end_s": 5.2}]}
```

Every time is in seconds from sending, measured on this host (the daemon's frames carry
no time). A model call ends at its `message_done`; it starts at the latest boundary
before its first frame (the echo, the previous `message_done`, the end of a tool or of a
confirmation), since the daemon sends no "call started" frame. A tool runs from its first
`activity` to a status other than `running` (`call` is the index of the model call that
asked for it). `stt_s` (spoken prompts) runs from releasing f4 to sending, recording
stop and transcription included. `ended_by`: `idle`, `error`, `stopped`, `next_prompt`,
`disconnect` or `reconnect`. The record holds **no prompt text and no tool titles**
(they may carry what was typed), only `chars`, kinds and times. Prompts from other
windows are not timed.

## Benchmark: `python -m jarvis.bench`

```bash
cd host
python -m jarvis.bench --dry-run               # list the fixed set; connects to nothing
python -m jarvis.bench                         # the questions only, 3 runs each
python -m jarvis.bench -n 5 --json baseline.json
python -m jarvis.bench --only hora --only catalog --include-pc   # ids or kinds; desktop needs the flag
python -m jarvis.bench --include-pc            # everything, PC tasks included (REAL desktop)
python -m jarvis.bench -n 5 --prompt "Di hola" # one prompt: echo, first text, total, medians
python -m jarvis.bench --list                  # running model, its thinking levels, all models
python -m jarvis.bench --thinking low          # model_set on the running model first (PERSISTS)
python -m jarvis.bench --model openai/gpt-6-luna --thinking off
python -m jarvis.bench --model openai/gpt-6.1-sol --thinking medium --list   # switch back, no prompt
```

The fixed set lives in [`jarvis/bench_requests.json`](jarvis/bench_requests.json) (`--set
FILE` reads another): 5 short questions, 6 catalog commands (`Abrí Spotify.`, volume,
pause, next song, browser) and 3 PC tasks. **Requests marked `desktop` act on the real
desktop and run only with `--include-pc`** (PC control must also be on, `/control on`);
naming one in `--only` without the flag is a usage error. With `--include-pc` each
confirmation the agent asks is asked at this terminal (`[y/N]`, no terminal denies) and
its time is reported as a person's wait, apart from the agent's; without it, any
confirmation is denied.

Each run goes through the window's own code (`JarvisClient`, `HostModel` and the turn
trace of [Turn log](#turn-log-every-stage-of-a-whole-turn)), from sending to `idle`. Per
run it prints total, first text and `2 calls 3.8s · 1 tool · wait 0.0s`; then, per
request, the **median / worst** total and model calls, the median wait, **`done`** (`k/n`
runs that did their job) and the protocol errors. A run is **not done** when the turn
failed, a tool ended other than `done` (e.g. PC control off: `Control de la PC apagado`),
or a catalog/PC request used no tool (the agent only answered that it cannot); its line
ends in `not done: error | tool failed | no tool`, and it is still timed. `--json FILE`
writes all of it: `model`, `thinking`, `runs`, `include_pc` and per request `total_s`,
`active_s` (total minus the person's wait), `model_calls` and `human_wait_s` as
`{median, worst}`, `done`, `not_done` (runs per reason), `errors`, and every run's turn
record with `done` and `not_done`.

It connects like the window (`--url`, `--host`, `--port`, `--token-file`; the token is
read from the file and never printed) and sends prompts into the daemon's **active
conversation**. **`--model` / `--thinking` send `model_set`, which the daemon saves as
its default**: note the `model:` line first and pass it back to restore. Exit codes: 0
ok, 2 usage (bad flags, unknown `--only`, a desktop request without `--include-pc`), 3
cannot connect (no daemon, token missing or refused), 4 the daemon answered an error
(printed as `code: message`, e.g. `thinking_unavailable`, `model_unavailable`,
`no_account`; with `--prompt` also a failed reply), 5 timed out (`--timeout`, default
120 s per run; the prompt is aborted).

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

`tests/test_voice.py::test_real_whisper_server_transcribes_a_wav` posts an existing WAV
(`JARVIS_WHISPER_WAV`) to the real `whisper-server` (`JARVIS_STT_URL`) and is skipped
unless `JARVIS_REAL_WHISPER=1`. The other push-to-talk tests use a fake recorder and an
in-process HTTP server: no microphone, no whisper-server.

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
`send`, `steer`, and `abort`, and answers a `confirm` ask with
`ui_response{requestId, confirmed}`. For `/login` it sends `auth_login{providerId, method}`
and `auth_reply{flowId, value | cancelled}`, and reads `auth_providers`,
`auth_prompt`, `auth_event` and `auth_done` (`auth_logout` exists in the client but
the window offers no sign-out). The message shapes are the ones in the fork's
`packages/protocol/src/index.ts`, the same ones the desktop app and the web UI use.
