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

## Tasks

- [x] T1 (S1, S5): cliente del backend + panel de ESP32 y turnos en la TUI, con
  pruebas. Inline. Rama `feat/host-backend-ws`. Commit: `feat(host): show esp32 state and voice turns`.
- [ ] T2 (S2): rediseño visual de la TUI; boceto aprobado por el usuario antes de
  programar. Rama propia. Commit: pendiente.
- [ ] T3 (S3): daemon upstream fijado como servicio de usuario de systemd. Commit:
  pendiente.
- [ ] T4 (S4): `backend/app/agents/gentle_dot.py` con daemon falso y luego real.
  Commit: pendiente.

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
