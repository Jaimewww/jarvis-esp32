# Pista S2: TUI, servicio de Gentle Dot y adaptador de tareas largas

Trabajo de la pista S2 según `docs/tareas.md` (P1-P4). Solo toca `host/` y
`backend/app/agents/gentle_dot.py`; un cambio de contrato va antes en su propia
solicitud sobre `docs/contratos.md`.

## Specs

- S1 (P1, `docs/tareas.md`): "Conectar la TUI al WebSocket del backend" — "La TUI
  muestra el estado del ESP32 y los turnos usando el simulador". Contrato C2 de
  `docs/contratos.md`: `ws://<pc>:8000/api/v1/ws?token=<t>`, token de
  `JARVIS_HOST_TOKEN` (por defecto `dev-host-token`), token incorrecto cierra con
  `4401`, JSON en tramas binarias, solo lectura, "el cliente ignora los tipos de
  evento y los campos que no conoce". Eventos: `esp32_snapshot`, `esp32_state`,
  `action`, `turn_started`, `turn_stage`, `turn_done`, `turn_error`.
- S2 (P2): "Mejorar la presentación de la TUI" — "Estado, conversación y acciones
  se leen de un vistazo".
- S3 (P3): "Daemon de Gentle Dot como servicio" — "Arranca solo, sin la aplicación
  de escritorio, y la TUI se conecta".
- S4 (P4): "Adaptador de Gentle Dot para tareas largas" — "Cumple la interfaz de
  agente del contrato C3 y pasa sus pruebas con un daemon real".
- S5: `host/` no tiene pruebas; las del cliente existente de Gentle Dot entran con
  P1 (servidor WebSocket falso, sin daemon real).
- S6 (L18): "lo que creo que no hay es CI acttions, asi que aprovechemois eso ya que
  estamos trabajando, agrega ese paso" — un flujo de GitHub Actions que en cada push y
  solicitud de fusión corra las pruebas automáticas que el repo ya tiene.

## Tasks

- [x] T1 (S1, S5): cliente del backend + panel de ESP32 y turnos en la TUI, con
  pruebas. Inline. Rama `feat/host-backend-ws`. Commit: `feat(host): show esp32 state and voice turns`.
- [ ] T2 (S2): rediseño visual de la TUI; boceto aprobado por el usuario antes de
  programar. Rama `feat/host-tui-views` (sobre `feat/host-backend-ws`).
  - [x] T2a: modelo compartido (`host/jarvis/model.py`) + vista desarrollador según
    `design/tui-developer/06-pipeline-refined.html` + tecla para alternar vistas
    (L9-L11). Worker. Commits: `5d0d92d` (modelo) y `0d3cdee` (vista y `f2`).
  - [x] T2b: sistema de temas compartido (tokens de `design/tui-theme/.preview/tokens.json`,
    modo oscuro y modo claro, L14-L16) + repintado de la vista desarrollador con las
    convenciones unificadas + vista usuario según
    `design/tui-user/08-story-chat-polished.html`. Worker. Commits: `ee1f26b` (tokens) y
    `5398f71` (vista usuario, `f3`, repintado). Correcciones del verificador en un tercer commit.
- [ ] T3 (S3): daemon upstream fijado como servicio de usuario de systemd.
  - [x] T3a: unidad, instalador, pruebas y docs, sin tocar el sistema. Rama
    `feat/gentle-dot-service`, commit `2a2ec81`, solicitud #5.
  - [x] T3b: instalación real en la máquina del usuario (L26).
- [ ] T4 (S4): `backend/app/agents/gentle_dot.py` con daemon falso y luego real.
  - [x] T4a: con daemon falso. Rama `feat/gentle-dot-agent`. Commit `c5b1c00`,
    solicitud #4 fusionada en `main`.
  - [ ] T4b: con daemon real; espera a T3. Rama `feat/gentle-dot-agent-real`. Parcial (L28):
    falta conectar una cuenta de IA en Gentle Dot para cerrar las pruebas que dependen de una respuesta.
- [x] T5 (S6): integración continua con GitHub Actions. Rama `ci/github-actions` desde
  `main`, worktree `jarvis-esp32-worktrees/pi-ci`. Worker. Commit: `b7a0f0e`, solicitud #3.

## Log

- L1 (usuario, 2026-11-19): "arranquemos con nuestra parte arma el plan y le damos,
  primero lo apruebo"
- L2 (usuario, 2026-11-19): "si dale vamos asi. y ya mergea lo de jaime" — plan
  P1→P4 aprobado; la solicitud #1 (contratos) se fusionó en `main` (3b6e1c4) tras
  una revisión nativa aprobada con 7 hallazgos no bloqueantes.
- L3 (evidencia): `backend/app/main.py` rechaza el token antes de aceptar el
  WebSocket, así que el cliente ve un rechazo HTTP 403 en el saludo y no el cierre
  `4401` del contrato C2. El cliente trata ambos como token rechazado.
- L4 (evidencia T1): 8 pruebas en `host/tests` pasan; corrida real con el backend y
  `tools/sim_device.py`: la TUI mostró `ESP32 esp32-s3 IDLE`, `BACKEND CONNECTED`,
  `turn … started`, `action media_key play_pause` y `turn … done — chat`. Con token
  incorrecto el cliente queda en `unauthorized` sin reintentar.
- L5 (diseño T2): bocetos en `design/tui-redesign/` (140×40, datos de demostración),
  pendientes de aprobación: `01-cockpit.html` (Cockpit), `02-timeline.html`
  (Timeline) y `03-voice-first.html` (Voice first); manifiesto en `design.json`.
- L6 (usuario, 2026-11-19, sobre `design/tui-redesign/`): "me gusta 2 y 3, pero
  mejoralas dale una mejor experiencia de usuario serian dos cosas diferentes version
  para desarrollador y version para usuario aparitr de eso creame 2 propuestas
  separadas y cada una de ellas con 5 opciones" — dos diseños nuevos:
  `design/tui-user/` (parte de 02 Timeline) y `design/tui-developer/` (parte de 03
  Voice first), cinco artboards cada uno. La 01 Cockpit queda descartada.
- L7 (diseño T2): pendientes de aprobación del usuario, `design/tui-developer/`
  tiene cinco artboards (`01-pipeline.html` Pipeline, `02-waterfall.html` Waterfall,
  `03-inspector.html` Inspector, `04-dashboard.html` Dashboard y `05-event-log.html`
  Event log) y `design/tui-user/` tiene cinco (`01-story.html` Story, `02-focus.html`
  Focus, `03-chat.html` Chat, `04-two-lanes.html` Two lanes y `05-calm.html` Calm);
  cada carpeta tiene su manifiesto `design.json`.
- L8 (usuario, 2026-11-19, sobre `design/tui-developer/` y `design/tui-user/`): "de
  desarrollador 1, trabaja sobre esa, pule y ordenad, me gusta la estructura pero un
  pelin de orden me gustaria. de usuario combina 1 y la de chat, me gusta la de chat
  pero esta muy simplona" — desarrollador: se refina 01 Pipeline en
  `design/tui-developer/06-pipeline-refined.html` conservando su estructura; usuario:
  se combinan 01 Story y 03 Chat en `design/tui-user/06-story-chat.html`. Ambos
  pendientes de aprobación antes de programar.
- L9 (usuario, 2026-11-19, sobre los dos artboards 06): "la de desarrollador lista. la
  de usuario ordenadala y jerarquiza mas veo mucho espacio muerto" — desarrollador:
  `design/tui-developer/06-pipeline-refined.html` APROBADO como boceto base de T2;
  usuario: `06-story-chat.html` se rehace en `design/tui-user/07-story-chat-dense.html`
  con más orden y jerarquía y sin espacio muerto, pendiente de aprobación.
- L10 (usuario, 2026-11-19, decisión): las dos versiones conviven como "Una app, tecla
  para cambiar": arranca en vista usuario y una tecla alterna a la vista desarrollador
  en la misma ventana.
- L11 (evidencia, exploración de `host/` y `docs/contratos.md`): la TUI actual es un
  solo `JarvisApp` sin modelo compartido. Datos del boceto de desarrollador: estado
  del dispositivo, conexiones, agente, cola y conversación ya llegan; `turn_stage` y
  `turn_error` están en el contrato C2 y el host los parsea, pero el backend aún no
  los emite ("pendiente"); medianas, sobre-objetivo y objetivos de carril (1.5 s / 4 s,
  D17) se calculan en el host. Huecos de contrato, sin cambiarlo en T2: `action` no
  trae `turn_id` ni estado (se asocia al turno abierto del mismo dispositivo) y el
  carril de un turno en vivo solo se conoce en `turn_done` (se muestra "—" hasta
  entonces). Tecla para alternar: `f2`. Mientras T2b espera, la vista usuario es la
  disposición actual.
- L12 (evidencia T2a): 26 pruebas en `host/tests` pasan (8 originales sin modificar,
  16 del modelo, 2 de la vista). Diferencias con el boceto: la fila de entrada es el
  Input compartido de Textual sin el glifo `▸`; el turno en vivo dice `lane — until
  done`; sin datos se muestran estados vacíos; por debajo de 120 columnas se ocultan
  las columnas de etapas y por debajo de 96 las barras. Sin corrida real contra el
  backend todavía. Corte de entrega: los dos commits de T2a suman ~1500 líneas sobre
  T1.
- L13 (usuario, 2026-11-19, con dos recortes de `06-story-chat`: la cabecera de estado
  y el margen izquierdo vacío del hilo): "quiero que mejores este apartado, mas
  elegante y wow. y aca hay un espacio sin usar, esas correciones puntuales, no tomes
  en cuenta tu version neuva la 07, no me gusto para nada" — la 07 queda DESCARTADA y
  se retira; se parte de `06-story-chat.html` con solo esas dos correcciones en
  `design/tui-user/08-story-chat-polished.html`, pendiente de aprobación.
- L14 (usuario, 2026-11-19, sobre `08-story-chat-polished`): "perfecto, antes de
  implementar que el modo user y dev, tengan una misma paleta de colores, tipografia y
  todo" — la 08 queda APROBADA como estructura de la vista usuario; antes de programar
  T2b se unifica el lenguaje visual de las dos vistas. A la pregunta de qué paleta
  queda: "dame propuestas y como quedarian en las dos pantallas en realidad" —
  propuestas de tema único en `design/tui-theme/`, cada una mostrando las dos
  pantallas reales; pendiente de elección. T2a se repinta después con el tema elegido.
- L15 (usuario, 2026-11-19): "y agregale un modo gentle sexy o el rosadito que tenemos
  ahora mismo" — se suman dos propuestas a `design/tui-theme/` con los valores reales
  de los temas de Gentle Shell: `05-gentleman-cute.html` (el rosa actual del usuario,
  tema `Gentleman-Cute`) y `06-gentleman-sexy.html` (`Gentleman-Sexy`). Seis en total.
- L16 (usuario, 2026-11-19, sobre `design/tui-theme/`): "ya listo lo que si esta
  decidio es que quiero modo dark y modo claro" — la app lleva un modo oscuro y un
  modo claro conmutables; el claro es `04-paper` (único claro propuesto). Cuál de los
  oscuros es el predeterminado queda por elegir; los tokens de los seis temas se
  registran y el predeterminado es una constante. Tecla para alternar oscuro/claro:
  `f3`. Las pruebas de T1 que fijaban el texto de la pantalla vieja se reescriben a
  propósito: la vista usuario aprobada no muestra detalle de desarrollador.
- L17 (usuario, 2026-11-19, decisión tras consultar al grupo): "ya esta decidido genmtle
  cute y el modo claro" — modo oscuro predeterminado: `gentleman-cute`
  (`design/tui-theme/05-gentleman-cute.html`); modo claro: `paper`
  (`04-paper.html`). Los otros cuatro temas quedan disponibles con `--theme`.
- L18 (usuario, 2026-11-19): "dale me parece bien ahi avanza lo mas que puedas y ve
  entregando todo, lo que creo que no hay es CI acttions, asi que aprovechemois eso ya
  que estamos trabajando, agrega ese paso" — orden aprobado: T2b, T4 con daemon falso,
  T3; entrega (push y solicitud de fusión) de cada unidad al terminarla; se agrega T5.
- L19 (evidencia T5): `.github/workflows/ci.yml` con `backend · pytest`, `host · pytest`
  (se omite mientras no exista `host/requirements-dev.txt`) y `pyright`; local: backend
  5 pruebas pasan, pyright 0 errores, actionlint sin hallazgos. Revisión nativa
  `review-c565724fd2f814b3` aprobada con 5 hallazgos no bloqueantes (acciones fijadas
  por versión mayor y no por SHA; `persist-credentials` por defecto; el paso del host
  queda en verde sin correr pruebas; push solo en `main`; el README aún dice que el
  host no tiene paso). Solicitud #3 abierta contra `main`.
- L20 (evidencia T2b): 69 pruebas en `host/tests` pasan. Sin corrida real por parte del
  worker (faltaba un entorno con fastapi); verificador independiente lanzado. No
  reproducido del boceto: el hilo no se desplaza, el medidor de voz es dibujado (el
  backend no envía nivel de audio), el cursor no parpadea. El eco `user_message` del
  daemon se pliega en el intercambio de voz si el texto coincide dentro de 120 s.
- L21 (usuario, 2026-11-19): "no me gusta eso de revision cruzada has que si apsa test
  ya se mergee" — cada solicitud de fusión se fusiona en cuanto pasan sus checks de CI,
  sin esperar revisión de un compañero. La #3 (CI) se fusionó en `main` con sus tres
  checks en verde. El repo no tiene protección de rama ni fusión automática habilitada
  (requiere administrador), así que la fusión la hace el agente tras ver los checks.
- L22 (evidencia T2b, verificador independiente): corrida real contra backend y
  simulador correcta; tema y vistas coinciden con los bocetos; `f2`, `f3`, `--theme` y
  cierre sin errores verificados. Seis defectos visibles a corregir antes de entregar:
  texto cortado en la tarjeta de Jarvis con el daemon caído; ~25 filas vacías bajo la
  cabecera con pocos intercambios; respuesta vacía en un turno en vivo; READY en verde
  con el daemon desconectado; columnas cortadas a 100 columnas; leyenda del pie
  incompleta. Corrección en curso.
- L23 (evidencia T4a): 31 pruebas del backend pasan (26 nuevas con daemon falso),
  pyright 0 errores; CI en verde y solicitud #4 fusionada. Huecos de C3 sin tocar el
  contrato: sin modelo de errores (`docs/contratos.md:122-140`), sin "tarea iniciada /
  terminada" (D14, `docs/decisiones.md:217`), sin mensaje para olvidar la conversación
  (`reset()` lanza `GentleDotUnsupported`), segundo pedido durante una tarea sin decidir
  (lanza `GentleDotBusy`). Sin verificar con daemon real: eco del texto, ids de mensaje
  separados, `stopped` tras `abort`.
- L24 (evidencia T2b): los seis defectos de L22 corregidos con una prueba cada uno
  (`host/tests/test_tui_states.py`); 87 pruebas pasan. Capturas revisadas: hilo anclado
  bajo "Today", `OFFLINE` apagado con el daemon caído, leyenda completa a 140 columnas.
  Textos elegidos por el agente, sin revisar por el usuario: pie `f2 view  f3 mode` y
  el mensaje "Gentle Dot isn't set up yet / start it once to type here".
- L25 (evidencia T3a): `host/systemd/` con unidad plantilla, `install.sh` y
  `test_install.sh` (38 comprobaciones pasan, `systemd-analyze --user verify` limpio);
  ExecStart `node <checkout>/packages/daemon/src/cli.ts` (Node >= 24), commit fijado
  `f97d56d` (`docs/decisiones.md:137-138`); el checkout local
  `_scratch-gentle-dot` está en ese commit pero sin `node_modules`. Revisión nativa
  `review-a11fb01a482ab7a2` aprobada con 9 hallazgos no bloqueantes. Nada instalado.
- L26 (evidencia T3b, 2026-11-19): `pnpm install` en `_scratch-gentle-dot`, unidad en
  `~/.config/systemd/user/gentle-dot.service`, servicio activo y habilitado en
  `127.0.0.1:4317`; linger sin activar.
- L27 (usuario, 2026-11-19): "frenamos aca, y continuamos en la siguiente sesion";
  retomado el 2026-11-20 con "dale continua".
- L28 (evidencia T4b, parcial): contra el daemon real, `ready` llega con
  `agentState: idle` y `model: "unknown"`; token incorrecto cierra con 4401; `abort` sin
  tarea no responde en 2 s. Sin cuenta de IA, un envío recibe
  `{"type":"error","code":"no_account","message":"Connect an AI account first: open Accounts or type /login."}`
  sin eco previo. El adaptador lo expone como `GentleDotNotSignedIn` con ese texto exacto y
  ya no envía `abort` ante un error previo al eco; la TUI lo muestra en ambas vistas.
  Backend 33 pasan y 5 omitidas (36 y 2 con `JARVIS_REAL_DAEMON=1`); host 89 y 4 (92 y 1).
  Sin verificar hasta conectar una cuenta: coincidencia del eco, ids de mensaje separados
  y `stopped` tras `abort`. `reset()` sigue imposible con `features.conversations: false`.
  pyright no se corrió localmente. Textos sin revisar por el usuario: `SIGN IN`,
  "no AI account in Gentle Dot", "connect one there, then type again", etiqueta `needs`.
