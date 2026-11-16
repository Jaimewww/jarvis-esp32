# TUI anfitriona de jarvis-esp32: cliente del daemon de Gentle Dot

La TUI es un cliente Python del protocolo WebSocket del daemon de gentle-dot
(fork `Jaimewww/gentle-dot`, rama `feat/linux-computer-use`). No toca el engine
ni el daemon: conecta a `ws://127.0.0.1:4317/ws`, se autentica con el token de
`~/.gentle-dot/token` y muestra la conversación y el registro de acciones.

## Specs

- S1: "mira como podemos hacer un fork de gentle-dot implementando una TUI propia y el soporte de computer use para linux" — la TUI vive en este repo (jarvis-esp32), el backend Linux en el fork.
- S2: "vamos con la 1, fork liviano" — TUI como cliente WS independiente; protocolo v1 del daemon (hello-first, token, mensajes de packages/protocol/src/index.ts).
- S3 (docs/tui.md de este repo): mostrar lo que dijo el usuario y la respuesta del asistente; registrar acciones sobre la PC y sus errores; reflejar reposo, escuchando, pensando, trabajando, error; indicar tareas en curso y fallos de conexión. ("escuchando/hablando" son locales del ESP32; la TUI muestra los estados del agente: idle/thinking/working/needs_you/restarting/error.)
- S4: catálogo acotado y rechazo de acciones desconocidas — la TUI solo muestra; las acciones las ejecuta el agente dentro del modelo de aprobaciones del fork (grant, blocklist, confirmación, stop).
- S5: Python 3 + `websockets` + `textual` (candidato documentado, ahora elegido); tests con un servidor WS falso, sin requerir un daemon real.

## Tasks

- T1 (S2,S3): doc ODD + estructura `host/` — hecho con este archivo.
- T2 (S2,S3,S5): cliente WS (`host/jarvis/client.py`): hello+token, reconexión con backoff, eventos ready/agent_state/user_message/message_delta/message_done/activity/error, send/abort.
- T3 (S3,S5): TUI Textual (`host/jarvis/tui.py`): conversación, estado del agente, registro de acciones, input, abortar.
- T4 (S5): tests con servidor falso + README/docs actualizados (docs/tui.md deja de decir "no existe implementación").

## Log

- L1: "vamos con la 1, fork liviano" (elección de variante; TUI = cliente WS, stack Python/Textual confirmado por el usuario al aprobar la variante 1).
- L2: el backend Linux del fork quedó verificado (589 tests, clippy limpio, commits 191e97a/eea1fd8 en feat/linux-computer-use).
- L3 (worker): host TUI implemented (client.py + tui.py + requirements + README), docs/tui.md updated.
