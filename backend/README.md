# Orquestador J.A.R.V.I.S. v0 — corre en la PC

Esqueleto del contrato de transporte v0 (decisión del 2026-11-16, ver
`docs/arquitectura.md` y `docs/nube.md`). Los contratos vigentes con el
dispositivo y el anfitrión están en [`docs/contratos.md`](../docs/contratos.md). Corre en la computadora del usuario; el
ESP32 lo alcanza por la LAN.

## Qué está implementado

- `POST /api/v1/converse` — recibe el WAV del ESP32 (multipart), lo guarda y
  responde `turn_id`, `reply_text`, `reply_audio_url` y `actions`, y emite
  `turn_started` y `turn_done` por el WebSocket. **Marcador de posición:** STT,
  agente (Gentle Shell) y TTS no están integrados; responde con un beep generado
  en el arranque y una acción de prueba.
- `POST /api/v1/heartbeat` — estado del ESP32 (`idle`/`recording`/`thinking`/
  `speaking`/`error` + RSSI e IP); lo reenvía a los clientes anfitriones como
  `esp32_state`.
- `WS /api/v1/ws?token=…` — canal del anfitrión: snapshot inicial
  (`esp32_snapshot`) y eventos `esp32_state`, `action`, `turn_started` y
  `turn_done`. Sin heartbeat durante
  15 s el dispositivo se marca `offline` y se notifica.
- `GET /api/v1/health` — prueba de vida y estado de los dispositivos.
- `app/ports.py` — interfaces `Transcriber`, `Agent` y `Synthesizer` (contrato C3).
- `app/fakes.py` — dobles de esas interfaces con salidas fijas, para pruebas.
- `app/agents/gentle_dot.py` — `GentleDotAgent`, el `Agent` del carril de tareas
  largas (D14) sobre el protocolo v1 del daemon de Gentle Dot (el mismo que usa
  `host/jarvis/client.py`). Probado contra un daemon falso y, de forma opcional,
  contra el daemon real (upstream `f97d56d`); **no está conectado a `/converse`**. Ver
  [adaptador de Gentle Dot](#adaptador-de-gentle-dot).

## Cómo correrlo

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Tokens por defecto (solo para desarrollo, redefinir con variables de entorno):
`JARVIS_DEVICE_TOKEN=dev-jarvis-token`, `JARVIS_HOST_TOKEN=dev-host-token`.

## Adaptador de Gentle Dot

Una conexión por `reply`: `hello` con el token, espera `ready`, envía
`{"type":"send","text":…}`, reconoce su eco `user_message` y entrega el mensaje del
asistente (`message_delta` … `message_done`) **frase por frase**, para que la primera
se sintetice mientras el daemon sigue (D17). `message_done.text` manda si se perdió un
delta. Una sola tarea a la vez: un segundo `reply` en curso falla con `GentleDotBusy`.

Configuración por variables de entorno (`GentleDotAgent.from_env()`); el token se lee
en cada `reply`, nunca al importar:

| Variable | Por defecto |
|---|---|
| `JARVIS_GENTLE_DOT_URL` | `ws://127.0.0.1:4317/ws` |
| `JARVIS_GENTLE_DOT_TOKEN` | sin valor; si está, gana sobre el archivo |
| `JARVIS_GENTLE_DOT_TOKEN_FILE` | `~/.gentle-dot/token` |
| `JARVIS_GENTLE_DOT_OPEN_TIMEOUT_S` | `5` (conexión y `ready`) |
| `JARVIS_GENTLE_DOT_IDLE_TIMEOUT_S` | `120` (máximo sin recibir ninguna trama) |

C3 no define errores; el adaptador lanza subclases de `GentleDotError`:

| Error | Cuándo |
|---|---|
| `GentleDotUnavailable` | No se puede conectar o el daemon cerró la conexión |
| `GentleDotUnauthorized` | Cierre `4401` (o HTTP 401/403 en el saludo) |
| `GentleDotProtocolMismatch` | Cierre `4400` |
| `GentleDotTokenError` | Sin token, archivo ausente o vacío; no se conecta |
| `GentleDotDaemonError` | Trama `error` (`code`, `message`) o `message_done.error` |
| `GentleDotNotSignedIn` | Subclase de la anterior: trama `error` con `code` `no_account` (Gentle Dot no tiene cuenta de IA). `str()` es el mensaje del daemon sin cambios, para mostrarlo tal cual: `Connect an AI account first: open Accounts or type /login.` El adaptador nunca inicia sesión |
| `GentleDotTimeout` | Sin `ready` en el plazo de apertura o sin tramas en el plazo de inactividad |
| `GentleDotStopped` | Otro cliente detuvo la respuesta (`message_done.stopped`) |
| `GentleDotBusy` | Ya hay una tarea en curso |
| `GentleDotUnsupported` | `reset()`: el protocolo v1 no tiene un mensaje para olvidar la conversación |

`abort()` envía `{"type":"abort"}` y la respuesta termina sin la frase cortada; sin
respuesta en curso no hace nada. Si el que llama cancela, o hay error o vence el
plazo antes de `message_done`, el adaptador también envía `abort` para no dejar al
daemon trabajando. Una trama `error` **antes** del eco `user_message` significa que el
daemon rechazó el envío sin empezar nada (por ejemplo `no_account`): entonces no se
envía `abort`, que podría detener la tarea de otro cliente.

### Daemon real (opcional)

`tests/test_gentle_dot_real.py` se salta salvo con `JARVIS_REAL_DAEMON=1` (CI no tiene
daemon) y usa `GentleDotAgent.from_env()`:

```bash
cd backend
JARVIS_REAL_DAEMON=1 .venv/bin/python -m pytest tests/test_gentle_dot_real.py -v -rs
```

Comprobado contra el servicio `gentle-dot.service` (upstream `f97d56d`): un token
incorrecto se cierra con `4401`; `abort` sin respuesta en curso no recibe nada; sin
cuenta de IA, `send` recibe solo
`{"type":"error","code":"no_account","message":"Connect an AI account first: open Accounts or type /login."}`
(sin eco) y el adaptador lanza `GentleDotNotSignedIn` con ese texto. Con cuenta, las
mismas pruebas comprueban el eco exacto del texto, que la respuesta tiene un
`messageId` distinto del del eco y que tras `abort` el daemon emite
`message_done` con `stopped: true`; sin cuenta se saltan citando el mensaje del daemon.
`ready` trae `features.conversations: false`; con eso el código de upstream responde a
`new_conversation` con `error` `conversations_off` ("This assistant keeps one continuous
chat."; leído en el código, no probado en vivo), así que `reset()` sigue sin soporte.

**Pendiente:** `reset()` (el daemon de servicio no admite `new_conversation`), la
confirmación de inicio y el aviso de fin de una tarea larga en segundo plano (D14; C3
solo define el flujo de texto) y correr las pruebas con respuesta (eco, ids, `stopped`)
con una cuenta de IA conectada.

## Pruebas

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

## Simulador del dispositivo

`tools/sim_device.py` hace un turno completo como el ESP32 (latidos, WAV a
`/converse`, descarga del audio). Ver [contratos](../docs/contratos.md#dobles-para-avanzar-sin-la-otra-parte).

```bash
python tools/sim_device.py --url http://127.0.0.1:8000 --token dev-jarvis-token
```

## Prueba con curl

```bash
# salud
curl -s http://127.0.0.1:8000/api/v1/health

# heartbeat del ESP32 (simulado)
curl -s -X POST http://127.0.0.1:8000/api/v1/heartbeat \
  -H "X-Device-Token: dev-jarvis-token" \
  -H "Content-Type: application/json" \
  -d '{"device_id":"esp32-s3","state":"idle","rssi":-52,"ip":"192.168.1.50"}'

# conversación (cualquier WAV sirve para el esqueleto)
curl -s -X POST http://127.0.0.1:8000/api/v1/converse \
  -H "X-Device-Token: dev-jarvis-token" \
  -F "audio=@test.wav;type=audio/wav"
```

## Lo que falta (en orden)

1. Integrar STT local (Whisper vs Parakeet, comparar y decidir midiendo).
2. Enchufar Gentle Shell como agente y definir el esquema de acciones reales.
3. Elegir y probar TTS con el ESP32 (formato y sample rate de salida).
4. Que el daemon del fork reenvíe `esp32_state` a sus clientes (la TUI ya
   consumiría el evento).
5. Lo pendiente de los contratos: `409` ante un segundo turno, eventos
   `turn_stage` y `turn_error`, y el resultado `{ok, detail}` de las acciones.
