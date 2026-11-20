# Pista S2, segunda vuelta: login en la TUI, instalador, agilidad, Whisper y docs

Ideas sueltas del usuario del 2026-11-20 (L1), ordenadas en tareas. Cada tarea va en su
propia solicitud desde `main`; worktree `jarvis-esp32-worktrees/pi-next`.

## Specs

- S1 (L1): "Que la TUI de Jarvis maneje /login, para conectar la cuenta sin ir al
  navegador." Protocolo del daemon: `auth_list`, `auth_login`, `auth_reply`,
  `auth_logout` / `auth_providers`, `auth_prompt` (`text|secret|select|manual_code`),
  `auth_event` (`info|auth_url|device_code|progress`), `auth_done`. El secreto nunca se
  registra ni se guarda.
- S2 (L1): "Corregir lo que encontró la revisión del instalador de systemd: no reintenta
  después de un daemon-reload fallido; no tiene límite de arranques; no revisa si el
  checkout tiene cambios sin commitear; no hace backup."
- S3 (L1): "Poner al día tu checkout local de main, que sigue en la PR #1, sin tocar tus
  bocetos de design/."
- S4 (L1): "Borrar los worktrees de ramas ya fusionadas, como pi-ci y pi-agent." Sin
  perder lo no versionado de `pi` (`design/**/.preview`, `docs/guia-pantalla/`).
- S5 (L1, L2): "creo que se demora mucho en responder, tenemos que hallare el equilibro";
  "osea creo que el modelop mucho piensa, quiero mas agilidad en las respuestas".
- S6 (L1, L2): "dejes listo para comenzar a probar con el whisper local que tengo"; "lo
  hago yo y le aviuso a jhaime por whastapp" (tarea V1 de la pista de voz, solicitud
  aparte); "Instalar la versión con CUDA ya" (whisper.cpp 1.9.5 hoy es solo CPU; RTX 4050
  6 GB; modelo `~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin`).
- S7 (L1, L2): "documentes como estamos ahciendo todo en quarto" — "Sitio Quarto en docs/
  (HTML)".
- S8 (L1): "actualices el readme que quede super visual". `README.md` es compartido: va en
  su propia solicitud.
- S9 (L10): "en la tui de dev no veo cuanto tarda en responder cada rquest"; aceptado:
  "si dale hazlo" para (1) medir hasta que el agente vuelve a idle y no hasta el primer
  `message_done` (un pedido con herramienta trae un mensaje vacío y después el texto),
  (2) mostrar el tiempo de cada respuesta al lado de cada `jarvis` en la conversación,
  (3) una prueba con el caso de herramienta.
- S10 (L11): "quiero que jarvis controle entera la pc, creo que es una fnciuonadlidad extra
  que nos toca, verdaD?"; "dale entonces diagnostica, actualiza decesiones para que es parte
  sea mia y vamos probando por secciones".
- S11 (L12): "y no podemos agrgar en jarvios un shorucut para grabar directo desde alla?";
  elegido: "Whisper → Gentle Dot (como escribir)": la TUI graba con el micrófono, transcribe
  con el whisper-server local y manda el texto al agente.
- S12 (L14): resguardos de la etapa b aprobados ("si dale de acuerdo en los puntos"):
  (1) control apagado por defecto, se enciende desde la TUI y vence a los 30 min;
  (2) parada de emergencia `Alt+Shift+Esc` que quita el permiso y corta la tarea;
  (3) lista de bloqueo (gestores de contraseñas, ajustes del sistema, diálogos de seguridad,
  por `app_id` de `niri msg -j focused-window`) y confirmación en la TUI ante enviar, pagar,
  comprar, eliminar (send, pay, buy, delete).
  Contrato compartido entre la extensión y la TUI:
  - Permiso: `$XDG_RUNTIME_DIR/jarvis/control.json`, modo 0600, `{"enabled": true,
    "expires_at": "<ISO-8601 UTC>", "granted_at": "<ISO-8601 UTC>"}`. Ausente, vencido, ilegible
    o `enabled` falso = denegado. Lo escribe y borra solo la TUI y la parada; la extensión lo lee
    en cada llamada, sin caché.
  - Confirmación: la extensión llama `ctx.ui.confirm` con `timeoutMs` 30000; el daemon lo manda
    a los clientes como `{"type":"ask","ask":{"method":"confirm",...}}`; la TUI contesta
    `{"type":"ui_response","requestId":...,"confirmed":true|false}`. Sin respuesta = denegado.
  - Parada: borrar el archivo de permiso es la llave principal; además manda `{"type":"abort"}`
    al daemon.
- S13 (L15): "Barrera real: ayudante aparte" — un servicio separado es el único con acceso a
  la pantalla; el daemon pierde el acceso directo a Wayland y abre apps o teclea pidiéndoselo.
  Reemplaza el archivo de permiso de S12 (el agente podía falsificarlo). Diseño:
  - **Ayudante `jarvis-input`** (servicio de usuario en `graphical-session.target`, Python
    stdlib, instalado en `~/.local/share/jarvis/input/`): único proceso que corre `wtype`,
    `grim`, `niri msg`, `gtk-launch` y `xdg-open`. Estado del permiso en memoria (reiniciar el
    ayudante = permiso revocado). Dos sockets Unix, JSON por líneas:
    - `$XDG_RUNTIME_DIR/jarvis/agent.sock` (lo usa la extensión del agente):
      `{"op":"open","app":"<desktop-id>"}` o `{"op":"open","url":"https://…"}` (sin permiso:
      etapa a); `{"op":"type","text":…,"intent":…}`, `{"op":"key","keys":…,"intent":…}`,
      `{"op":"screenshot","scale":0.5}` (requieren permiso). Respuesta `{"ok":true,...}` o
      `{"ok":false,"error":"…"}`; captura: `{"ok":true,"png_base64":"…"}`.
    - `$XDG_RUNTIME_DIR/jarvis-control/control.sock` (solo la TUI y `jarvis-stop`):
      `{"op":"grant","minutes":30}` (máximo 30) → `{"ok":true,"expires_at":"…Z"}`;
      `{"op":"revoke"}`; `{"op":"status"}`; `{"op":"subscribe"}` → eventos
      `{"event":"state","enabled":…,"expires_at":…}` y
      `{"event":"confirm","id":…,"title":…,"message":…,"timeout_s":30}`; respuesta del cliente
      `{"op":"answer","id":…,"allow":true|false}`. Sin suscriptor o sin respuesta = denegado.
  - El ayudante hace todos los controles de S12 (3): lista de bloqueo por `app_id`/título,
    foco ilegible = denegado, re-chequeo de foco justo antes de `wtype`, palabras de riesgo
    por raíz (envi*, pag*, compr*, elimin*, borr*, submit, send*, pay*, buy*, delet*), y
    confirmación de `Return`/`ctrl+Return` en navegadores, correo y mensajería; límite de 10
    acciones por segundo; texto máximo 2000 caracteres; `argv` sin shell.
  - **Daemon aislado** (unidad `gentle-dot.service`): sin acceso a los sockets de Wayland, X11,
    niri, al bus de sesión ni a `jarvis-control/`; solo lectura sobre el ayudante instalado,
    `~/.config/systemd/user`, `~/.local/bin/jarvis-stop` y las extensiones del agente. Se
    prueba en la máquina que Gentle Dot sigue andando (chat, `/login`, modelos).
  - **Parada** `jarvis-stop`: `revoke` por `control.sock` y `abort` al daemon.
- S14 (L16): prueba guiada: el agente no encuentra la app ("No pude abrir Notas: `gtk-launch
  terminó con código 2`") y siempre teclea en la terminal de la TUI ("la ventana activa es una
  terminal y está bloqueada"). Agregar al protocolo de `agent.sock`:
  `{"op":"apps"}` → `{"ok":true,"apps":[{"id":"<desktop-id>","name":"…"}]}` (entradas `.desktop`
  visibles de los `XDG_DATA_DIRS` del ayudante; sin permiso);
  `{"op":"windows"}` → `{"ok":true,"windows":[{"id":…,"app_id":…,"title":…,"focused":…}]}`
  (requiere permiso); `{"op":"focus","window_id":…}` (requiere permiso; se niega si la ventana
  está en la lista de bloqueo o es una terminal). Flujo del agente para teclear: abrir o elegir
  la ventana, enfocarla, y recién ahí `type`/`key`.
- S15 (L17): el verificador dio FAIL a S13 como barrera: `open` sin permiso lanza código que el
  daemon puede reescribir (`~/.local/bin`, `~/.config/fish`, `mimeapps.list`, AppImages), puede
  crear drop-ins de systemd (`~/.config/systemd/user.control`, `~/.local/share/systemd`,
  `environment.d`), reescribir la TUI (`~/.local/bin/jarvis`, su código y su `.venv`) y sombrear
  `jarvis-stop` en el PATH de niri; además la config de niri es escribible. Decisión del
  usuario: "Home solo lectura + carpeta de trabajo". Requisitos:
  - El daemon ve `$HOME` en solo lectura (`ProtectHome=read-only` o equivalente) y solo escribe
    en `~/.gentle-dot` y en la carpeta de trabajo `~/Jarvis` (se crea si falta). Sin acceso al
    bus del sistema (`/run/dbus/system_bus_socket`).
  - `open`: solo apps cuyo `Exec` resuelve a un ejecutable fuera de `$HOME` y que no sean
    terminales ni `Terminal=true`; URLs `http(s)` abiertas con el navegador por defecto
    resuelto por el ayudante sin `xdg-open` genérico (o `xdg-open` con `mimeapps.list` protegido,
    que ya queda en solo lectura para el daemon).
  - `jarvis-input` arranca con entorno mínimo (`UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH`,
    `PATH=/usr/local/bin:/usr/bin`).
  - La TUI se instala como el ayudante (copia en `~/.local/share/jarvis/tui` con su propio
    entorno de Python, y `~/.local/bin/jarvis` apuntando ahí); el daemon no puede modificarla.
  - `jarvis-stop` usa `PATH=/usr/local/bin:/usr/bin` y `/usr/bin/python3`; el atajo de niri
    usa la ruta absoluta.
- S16 (L20): "oye y no hay como que cuando le pide por ejemplo irse a vscode ese sea el foco?,
  porque tipo lo abre y me cambio de foco y ya no anda". `type` y `key` aceptan
  `"window_id": N` opcional: el ayudante, en un solo paso y con el mismo candado, aplica los
  controles de `focus` a esa ventana, la enfoca, confirma el foco, teclea, y al terminar
  devuelve el foco a la ventana que lo tenía antes (si sigue existiendo). Sin `window_id`,
  igual que hoy. La extensión pasa `window_id` desde `pc_type`/`pc_key` y AGENTS.md indica
  usarlo siempre que sepa la ventana destino.

## Tasks

- [x] T1 (S3): `main` local adelantado a `afa7124` con `--ff-only`; `design/` intacto.
- [x] T2 (S4): rescatar lo no versionado de `pi` al checkout principal, entornos nuevos
  en `pi-next`, quitar `pi` y `pi-agent` y sus ramas fusionadas. `pi-ci` ya quitado. Inline. Hecho: copia verificada con `diff -rq`, ramas locales fusionadas borradas (queda `docs/guia-pantalla`, sin fusionar).
- [x] T3 (S2): instalador de systemd: reintento del `daemon-reload`, límite de arranques
  (ya existe en la unidad: fijarlo con una prueba), revisión de checkout sucio, backup de
  la unidad previa. `host/systemd/`. Rama `fix/systemd-installer`. Worker. Evidencia L5.
- [x] T4 (S1): `/login` en la TUI. `host/jarvis/`. Commits `d361a3a`, `886bf7a`, fusión `0c2c810`; solicitud #14. Evidencia L7.
- [x] T5 (S5): medir el camino escrito (eco, primer texto, fin) y bajar el razonamiento o
  elegir un modelo más ágil vía el daemon. `host/`, `gentle_dot.py`. Commit: pendiente.
- [ ] T6 (S6): whisper.cpp con CUDA en el sistema, `whisper-server` como servicio de
  usuario con el modelo cargado una vez, medición con un WAV en español. Inline.
- [ ] T7 (S6): V1 — transcriptor real detrás de `Transcriber` en `/converse`, con el tiempo
  de la etapa `stt`. `backend/`. Solicitud aparte para Jaime. Commit: pendiente.
- [x] T8 (S7): sitio Quarto en `docs/` sobre cómo trabajamos (ODD, pistas, worktrees, CI,
  solicitudes). Commit: pendiente.
- [x] T9 (S8): `README.md` visual. Solicitud propia. Commit: pendiente.
- [ ] T10 (S9): tiempos por respuesta en la vista desarrollador. `host/`. Rama
  `fix/typed-reply-timing`. Commit `f013a55`, solicitud #16.
- [x] T11 (S10): diagnóstico de "abre spotify"/"abre brave". Evidencia L11.
- [ ] T12 (S10): `docs/decisiones.md` D20/D21: el control de la PC pasa a S2, por etapas.
  Rama `docs/pc-control-s2`, solicitud propia (archivo compartido).
- [ ] T13 (S10, etapa a): el servicio del daemon arranca dentro de la sesión gráfica
  (`graphical-session.target`) para heredar `WAYLAND_DISPLAY`; prueba abriendo una app.
  `host/systemd/`.
- [ ] T14 (S10, S12, etapa b): rama `feat/pc-control-stage-b`.
  - [ ] T14a: extensión de Pi `host/gentle-dot/extensions/pc-control.ts` (herramientas
    `pc_type`, `pc_key`, `pc_screenshot`; bloqueo de `wtype`/`grim` por `bash`), instalación,
    y script de parada `host/bin/jarvis-stop`.
  - [ ] T14b: TUI: `/control on|off|status`, cuenta regresiva, y respuesta a los `ask` de
    confirmación.
- [ ] T15 (S10, etapa c): cursor.
- [ ] T16 (S11): grabar desde la TUI (tecla para hablar). `host/`. Rama
  `feat/tui-push-to-talk`. Commit: pendiente.
- [ ] T17 (S10, etapa a): reglas del agente para abrir apps sin bloquear
  (`~/.gentle-dot/agent/AGENTS.md`), versionadas en `host/` e instaladas por `install.sh`.
  Rama `fix/agent-gui-launch`.
- [ ] T18 (S13): ayudante `jarvis-input` (`host/input/`).
- [ ] T19 (S13): extensión del agente sobre `agent.sock` (+ `pc_open_app`) y `jarvis-stop`
  sobre `control.sock` (`host/gentle-dot/`, `host/bin/`).
- [ ] T20 (S13): TUI sobre `control.sock` (`/control`, confirmaciones) (`host/jarvis/`).
- [ ] T21 (S13): unidades e instalador: `jarvis-input.service`, aislamiento de
  `gentle-dot.service` (`host/systemd/`).
- [ ] T22 (S14): ops `apps`, `windows`, `focus` en el ayudante (`host/input/`) y herramientas
  `pc_list_apps`, `pc_list_windows`, `pc_focus_window` + reglas en `AGENTS.md`
  (`host/gentle-dot/`).
- [ ] T23 (S15): endurecer `open` (`host/input/`).
- [ ] T24 (S15): home en solo lectura, carpeta `~/Jarvis`, bus del sistema, entorno de
  `jarvis-input`, instalación de la TUI y `jarvis-stop` (`host/systemd/`, `host/bin/`).
- [x] T25 (S16): tecleo con `window_id` (enfocar, teclear y devolver el foco) en el ayudante y
  la extensión. Commit `82b2b41`.

## Log

- L1 (usuario, 2026-11-20): " 1. Que la TUI de Jarvis maneje /login, para conectar la
  cuenta sin ir al navegador.
   2. Corregir lo que encontró la revisión del instalador de systemd:
       - no reintenta después de un daemon-reload fallido;
       - no tiene límite de arranques;
       - no revisa si el checkout tiene cambios sin commitear;
       - no hace backup.
   3. Poner al día tu checkout local de main, que sigue en la PR #1, sin tocar tus bocetos
   de design/.
   4. Borrar los worktrees de ramas ya fusionadas, como pi-ci y pi-agent. . hagamos esto y
   adicional creo que se demora mucho en responder, tenemos que hallare el equilibro y
   luego quiero que dejes listo para comenzar a probar con el whisper local que tengo. de
   igual amnera documentes como estamos ahciendo todo en quarto y actualices el readme que
   quede super visual, son ideas sueltas, asi que ordenalas y arma el plan"
- L2 (usuario, respuestas): demora: "osea creo que el modelop mucho piensa, quiero mas
  agilidad en las respuestas"; Whisper: "lo hago yo y le aviuso a jhaime por whastapp";
  GPU: "Instalar la versión con CUDA ya"; Quarto: "Sitio Quarto en docs/ (HTML)".
- L3 (evidencia): `/converse` sigue siendo un simulacro (`backend/app/main.py:213-253`);
  `turn_stage` pendiente; `gentle_dot.py` no fija modelo, lo decide la cuenta del daemon.
  whisper.cpp 1.9.5 y ggml 0.29.0 sin backend CUDA (`/usr/lib/ggml/` solo CPU); no hay
  `whisper-server` corriendo. Quarto 1.11.9 en `~/.local/bin`. La unidad ya trae
  `StartLimitIntervalSec=10min` y `StartLimitBurst=10` (`gentle-dot.service.in:18-19`).
- L4 (evidencia T6): `extra/ggml-cuda 0.29.0-2` existe (depende de `cuda`, `nccl`); instalar
  necesita la contraseña de sudo del usuario.
- L5 (evidencia T3): `test_install.sh` 68 pasan y 0 fallan (eran 38); RED con 8 fallos antes
  del cambio. Marcador `gentle-dot.service.reload-pending` para reintentar el
  `daemon-reload` (también en `--uninstall`), `--allow-dirty` frente a `git status
  --porcelain`, copia `gentle-dot.service.bak`, límite de arranques fijado por prueba. Sin
  shellcheck (no instalado); no probado contra el systemd real. El checkout de Gentle Dot
  tiene `.atl/` sin seguimiento (caché de agente del 2026-11-18): con la nueva revisión,
  reinstalar exige borrarlo o `--allow-dirty`.
- L6 (evidencia T9, T8): README visual en la solicitud #10; sitio Quarto en `docs/sitio/` en la
  #11 (`quarto render docs/sitio` sin advertencias, 4 páginas). Diagramas Mermaid sin mirar en
  navegador.
- L7 (evidencia T4): verificador independiente (riesgo 2, credenciales) falló dos veces: la
  clave quedaba visible tras `Esc` y podía salir como chat; un corte dejaba el flujo trabado;
  un flujo viejo trababa `/login`; `Ctrl+C` copiaba la clave oculta. Todo corregido con
  pruebas; veredicto final PASS en `0c2c810`. Host 143 pasan y 5 omitidas. Pendiente menor:
  `Esc` doble antes de que el daemon nombre el flujo, si ese flujo termina sin tramas, hace
  que el siguiente `/login` se cancele solo (sale con `Esc` doble).
- L8 (evidencia T5, solicitud #12): medianas con "Responde solo: ok", N=3: gpt-6.1-sol medium
  5,07 s; sol low 3,36 s; gpt-6.1-terra low 2,88 s; gpt-6-luna off 2,57 s; eco 0,03 s; gpt-5.x
  falla en esta cuenta ("Something went wrong while answering"). El usuario eligió "gpt-6.1-sol
  en low (3,4 s)"; aplicado con `python -m jarvis.bench --model openai/gpt-6.1-sol --thinking
  low` (mediana medida al aplicar 3,97 s, una corrida de 5,3 s).
- L9 (evidencia T7, solicitud #13 abierta para Jaime): `WhisperServerTranscriber` con
  `JARVIS_STT=whisper`, `turn_stage` de `stt` y `turn_error` con 502. Backend 53 pasan y 6
  omitidas. En CPU, unos 15 s por clip; sin WAV con voz real en español. Dos revisiones
  nativas aprobadas con hallazgos no bloqueantes (excepciones fuera de `WhisperServerError`
  dejan el turno abierto; `JARVIS_STT_TIMEOUT_S` sin validar).

- L10 (usuario, 2026-11-20, con captura): "en la tui de dev no veo cuanto tarda en responder
  cada rquest o depende de abckend y por eso no sale?"; luego "si dale hazlo". Evidencia:
  la fila `reply` mostraba `first text — · total 4.6s` tras "abre spotify": `_time`
  (`host/jarvis/model.py:664-686`) cierra la medición en el primer `message_done`, que fue un
  mensaje vacío de herramienta; el texto "No pude abrir Spotify…" llegó en un segundo mensaje
  sin medir.
- L11 (evidencia T11, 2026-11-20): el daemon sí ejecuta órdenes (`xdg-open
  'https://open.spotify.com'`, `brave`), pero el proceso del servicio no tenía
  `WAYLAND_DISPLAY` ni `DISPLAY` (arrancó 05:41, antes de la sesión gráfica); Brave:
  `Missing X server or $DISPLAY`. Tras `systemctl --user restart gentle-dot` el proceso tiene
  ambas variables y "Abrí el navegador Brave" abrió una ventana nueva (Brave ya corría).
  El registro también explica gpt-5.x: "The 'gpt-5.4-mini' model is not supported when using
  Codex with a ChatGPT account." `wtype` no está instalado; `grim` y `gtk-launch` sí.
- L12 (usuario y evidencia, 2026-11-20): T6 cerrada: `ggml-cuda` instalado, whisper.cpp usa
  CUDA0; servicio de usuario `whisper-server` (solo en la máquina) en `127.0.0.1:8080`; un audio
  de 3 s tarda ~0,18 s (antes ~15 s en CPU); la voz del usuario (`~/jarvis-prueba.wav`, 6,4 s)
  dio "Hola, hola, hola, hola. ¿Qué tal? ¿Qué onda?" en 236-334 ms. Luego: "y no podemos
  agrgar en jarvios un shorucut para grabar directo desde alla?"; respuesta: "Whisper → Gentle
  Dot (como escribir)".
- L13 (usuario y evidencia, 2026-11-20): "paso algo raro, lo abrio pero en el chat dice que no
  pudo". Whisper oyó "Jarvis abre break"; el agente corrió `brave` en primer plano, la
  herramienta cortó a los 10 s ("Command timed out after 10 seconds") y respondió que no pudo
  confirmarlo, aunque Brave abrió. Con `~/.gentle-dot/agent/AGENTS.md` (lanzar con `setsid -f`
  o `gtk-launch` y confirmar con `pgrep`) y el servicio reiniciado, "Abrí Brave." corrió
  `setsid -f brave >/dev/null 2>&1; pgrep -f brave` y respondió "Listo, abrí Brave." (4,2 s).

- L14 (usuario y evidencia, 2026-11-20): "si dale de acuerdo en los puntos, ya intalo wtype".
  Exploración: el daemon fija `@earendil-works/pi-coding-agent` 1.5.0; las extensiones se
  cargan con jiti desde `~/.gentle-dot/agent/extensions/*.ts` o rutas en `settings.json`;
  `pi.registerTool`, `pi.on("tool_call")` con `{block:true}`, `ctx.ui.confirm`; el daemon
  reenvía los pedidos de UI como `ask` (`protocol/src/index.ts:22-34, 433`) y acepta
  `ui_response` (`:353`) y `abort` (`:352`). niri: `~/.config/niri` es un enlace a
  `~/dotfiles/config/niri/` (atajos en `binds.kdl`).

- L15 (evidencia y usuario, 2026-11-20): el verificador independiente dio FAIL a S12 (1) como
  barrera: desde `bash` el agente falsifica `control.json` (ruta relativa o Python) y corre
  `wtype`/`grim` disfrazados (`x=wty; ${x}pe`, `w''type`, base64, `subprocess`), y puede plantar
  extensiones o editar `settings.json`; los demás controles pasan tal como se especificaron.
  Con `wtype` instalado, el agente ya puede teclear sin resguardos. Pregunta: "¿Cómo
  seguimos?"; respuesta: "Barrera real: ayudante aparte".

- L16 (usuario y evidencia, 2026-11-20): prueba guiada. Sin permiso: "No escribí nada porque el
  control del escritorio está apagado" (correcto). `jarvis` corre `main` y no tenía F5 ("no
  fuinciona el f5"); desde la rama sí. Registro del ayudante: permiso concedido, revocado y
  concedido (F5 alterna); "abri notas y escribe hola mundo" → `gtk-launch org.gnome.TextEditor`
  terminó con código 2 (la app real es `dev.duxlabs.shell.notepad`); "ya abri yo, escribe" →
  denegado porque el foco era la terminal de la TUI (`com.mitchellh.ghostty`).

- L17 (evidencia y usuario, 2026-11-20): segundo verificador: el aislamiento cierra todo lo
  previsto (runtime, X11, uinput, docker, `/proc` de niri, Landlock), pero `open` sin permiso
  + home escribible = escape; drop-ins de systemd, TUI escribible, `jarvis-stop` sombreable,
  niri escribible. Pregunta: "¿Qué preferís?"; respuesta: "Home solo lectura + carpeta de
  trabajo".
- L18 (evidencia, 2026-11-20): tercer verificador: L17 (a)-(c) cerrados; FAIL por `/tmp`,
  `/var/tmp` y `/dev/shm` compartidos (caché jiti de `pi` sin aislar, `SingletonSocket` de
  Brave) y porque el atajo de parada todavía no está en niri. Arreglo: `PrivateTmp=disconnected`
  y `TemporaryFileSystem=/dev/shm` (`test_install.sh` 201 pasan); reinstalado: desde el daemon
  `/tmp` es un tmpfs propio (sin `jiti` ni `org.chromium*`), `/dev/shm` y `/var/tmp` vacíos, y
  el chat responde (4,55 s). Riesgo anotado: un `.git` con hooks en `~/Jarvis` se ejecutaría al
  entrar con un prompt que lea git.
- L19 (usuario y evidencia, 2026-11-20): Jarvis abrió VS Code, enfocó, `ctrl+n` y escribió, pero
  no apareció nada. Prueba del usuario `sleep 4; wtype hola` en VS Code: "solo pone la". Causa:
  apps Chromium/Electron pierden las primeras teclas mientras aplican el keymap que `wtype`
  sube en cada corrida. `wtype -s 250` antes no alcanzó (Jarvis escribió "la mundo"): el keymap
  viaja con la primera tecla. Arreglo final: `wtype -k Shift_L -s 250 -d 8 …` (un Shift inocuo
  lleva el keymap, pausa, y recién el texto). Prueba del usuario: "ahora si aparece hola completo".

- L20 (usuario, 2026-11-20): "ahora si aparece hola completo" (arreglo del keymap confirmado).
  Luego: "oye y no hay como que cuando le pide por ejemplo irse a vscode ese sea el foco?,
  porque tipo lo abre y me cambio de foco y ya no anda".
- L21 (usuario y evidencia, 2026-11-20): con T25 instalado y el foco en otra ventana: "puso "la mundo"
  de nuevo". El log muestra `key ctrl+n` y luego `type` con `window_id` 69 (VS Code): cada acción
  enfoca VS Code y devuelve el foco, y wtype corre apenas niri confirma el foco; Electron todavía no
  devolvió el foco al editor. Arreglo: pausa de 400 ms tras cambiar el foco, y no se re-enfoca si
  la ventana destino ya tiene el foco.
- L22 (usuario, 2026-11-20): "ahora ya funciona" (T25 + pausa de 400 ms confirmados en el
  escritorio real, con el foco en otra ventana).
