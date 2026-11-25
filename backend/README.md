# Orquestador J.A.R.V.I.S. v0 — corre en la PC

Esqueleto del contrato de transporte v0 (decisión del 2026-11-16, ver
`docs/arquitectura.md` y `docs/nube.md`). Los contratos vigentes con el
dispositivo y el anfitrión están en [`docs/contratos.md`](../docs/contratos.md). Corre en la computadora del usuario; el
ESP32 lo alcanza por la LAN.

## Qué está implementado

- `POST /api/v1/converse` — recibe el WAV del ESP32 (multipart), lo guarda y
  responde `turn_id`, `reply_text`, `reply_audio_url` y `actions`, y emite
  `turn_started`, `turn_stage` (`stt`) y `turn_done` por el WebSocket. El WAV se
  transcribe con el `Transcriber` que elige `JARVIS_STT` (`fake` por defecto,
  `whisper` con whisper.cpp local: ver [STT con whisper.cpp local](#stt-con-whispercpp-local)).
  **Marcador de posición:** el agente (Gentle Shell) no está integrado; la frase y
  la acción siguen siendo de prueba. El audio es un pitido por defecto, o «En
  orden, señor.» hablado si se configura [Piper](#voz-local-piper-y-whisper).
- `POST /api/v1/heartbeat` — estado del ESP32 (`idle`/`recording`/`thinking`/
  `speaking`/`error` + RSSI e IP); lo reenvía a los clientes anfitriones como
  `esp32_state`.
- `WS /api/v1/ws?token=…` — canal del anfitrión: snapshot inicial
  (`esp32_snapshot`) y eventos `esp32_state`, `action`, `turn_started`,
  `turn_stage` (`stt`), `turn_done` y `turn_error` (solo si falla el STT). Sin heartbeat durante
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

## Voz local: Piper y Whisper

| Qué querés probar | Dependencias locales |
|---|---|
| TTS con Piper | Python (probado con 3.14), `requirements-voice.txt`, `ffmpeg` en `PATH` y una voz Piper `.onnx` con su `.onnx.json` |
| STT con Whisper | `whisper-server` de whisper.cpp (probado con 1.9.5), un modelo GGML (`ggml-large-v3-turbo-q5_0.bin` por defecto) y una revisión que incluya el [PR #13](https://github.com/Jaimewww/jarvis-esp32/pull/13) |

### Probar Piper

Desde la raíz, con el entorno virtual de arriba ya creado:

```bash
backend/.venv/bin/python -m pip install -r backend/requirements-voice.txt
mkdir -p "$HOME/.local/share/piper-voices"
backend/.venv/bin/python -m piper.download_voices \
  --data-dir "$HOME/.local/share/piper-voices" es_MX-ald-medium
PYTHON=backend/.venv/bin/python bash tools/probe-piper.sh \
  "$HOME/.local/share/piper-voices/es_MX-ald-medium.onnx" /tmp/jarvis-piper-probe.wav
```

El script usa la frase «En orden, señor.» y verifica que el WAV no esté vacío
ni sea silencioso y que sea PCM de **16 kHz, mono, 16 bits**. Para reproducirlo,
usá tu reproductor de audio o `ffplay -nodisp -autoexit /tmp/jarvis-piper-probe.wav`.

### Probar `/converse` con Piper

```bash
cd backend
JARVIS_PIPER_MODEL="$HOME/.local/share/piper-voices/es_MX-ald-medium.onnx" \
  .venv/bin/uvicorn app.main:app --port 8000
```

Con `JARVIS_PIPER_MODEL` el backend sintetiza la respuesta fija una vez, al
arrancar. Mandá un WAV con el [`curl` de abajo](#prueba-con-curl), descargá la
ruta relativa que devuelve `reply_audio_url` y comprobá que se escucha la
frase. Sin esa variable, se conserva el pitido y no hace falta Piper ni FFmpeg.

La síntesis corre en procesos hijos, no dentro del backend: `python -m piper`
(con el intérprete que ejecuta el servidor) y luego `ffmpeg`. Cada uno tiene un
límite duro de 30 s; si vence, el hijo se mata y se recoge antes de responder
con error. La espera no bloquea el bucle de eventos. Si el modelo no existe, falta
Piper o FFmpeg, o el hijo termina con error o timeout, el arranque falla con un
mensaje claro (por ejemplo `JARVIS_PIPER_MODEL does not point to a file: …`,
`Install requirements-voice.txt to use JARVIS_PIPER_MODEL` o
`Install ffmpeg and make it available on PATH to use JARVIS_PIPER_MODEL`);
**nunca** se reemplaza en silencio por el pitido cuando `JARVIS_PIPER_MODEL` está
configurada.

### Activar también Whisper cuando esté disponible

`whisper-server` y su modelo son requisitos del sistema, no paquetes de pip.
La [guía del adaptador de STT](#stt-con-whispercpp-local) documenta el modelo,
`tools/whisper-server.sh` y su puerto `8080`. Levantá ese servidor y agregá
`JARVIS_STT=whisper` al mismo comando de arranque del backend.

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

## STT con whisper.cpp local

`app/stt/whisper_server.py` (`WhisperServerTranscriber`) envía cada WAV a un
`whisper-server` de whisper.cpp que mantiene el modelo cargado: `POST` multipart con
`file`, `response_format=json`, `language` y `temperature=0.0`; responde
`{"text": " ..."}`. Comprobado con whisper.cpp 1.9.5 en CPU.

**1. Arrancar el servidor** (en primer plano; Ctrl-C para pararlo):

```bash
tools/whisper-server.sh            # modelo large-v3-turbo q5_0, español, 127.0.0.1:8080
tools/whisper-server.sh -t 8       # flags extra van a whisper-server (-ng: sin GPU)
```

| Variable | Por defecto |
|---|---|
| `JARVIS_WHISPER_MODEL` | `~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin` |
| `JARVIS_WHISPER_HOST` / `JARVIS_WHISPER_PORT` | `127.0.0.1` / `8080` |
| `JARVIS_WHISPER_BIN` | `whisper-server` |

**2. Arrancar el backend con Whisper:**

```bash
cd backend
JARVIS_STT=whisper .venv/bin/uvicorn app.main:app --port 8000
```

| Variable | Por defecto |
|---|---|
| `JARVIS_STT` | `fake` (CI y pruebas usan el doble); `whisper` usa el servidor; otro valor falla al importar |
| `JARVIS_STT_URL` | `http://127.0.0.1:8080/inference` |
| `JARVIS_STT_LANGUAGE` | `es` (también `auto`) |
| `JARVIS_STT_TIMEOUT_S` | `30` |

En cada turno el backend emite `turn_stage` con `stage: "stt"`, `ms` y `text` (lo
escuchado) entre `turn_started` y `turn_done`, y lo registra en su log
(`turn …: stt 812.3 ms: 'qué hora es'`). Si la transcripción falla emite un solo
`turn_error` (`stage: "stt"`, `message` con la URL) y responde `502` con el mismo
mensaje. Errores (subclases de `WhisperServerError`):

| Error | Cuándo |
|---|---|
| `WhisperServerUnavailable` | No conecta (servidor apagado, host o puerto mal) |
| `WhisperServerTimeout` | Sin respuesta en `JARVIS_STT_TIMEOUT_S` |
| `WhisperServerHTTPError` | Estado distinto de 200 (`status`); p. ej. `400 Invalid request` si no es WAV |
| `WhisperServerBadResponse` | 200 sin JSON, sin `text` de tipo texto, o con `error` |

**3. Un WAV en español.** El repositorio no trae uno (no hay TTS instalado para
generarlo de forma reproducible). Grabar unos segundos con PipeWire o ALSA, o convertir
cualquier grabación (D6: 16 kHz, mono, 16 bits):

```bash
pw-record --rate 16000 --channels 1 --format s16 clip.wav   # Ctrl-C para terminar
arecord -f S16_LE -r 16000 -c 1 -d 4 clip.wav               # alternativa (alsa-utils)
ffmpeg -i nota.m4a -ar 16000 -ac 1 -c:a pcm_s16le clip.wav  # desde otro formato
```

**4. Probar y medir:**

```bash
cd backend
# solo STT: texto y tiempo por envío (el primero calienta el modelo)
.venv/bin/python -m app.stt clip.wav --runs 3
# prueba opcional contra el servidor real (CI la salta)
JARVIS_REAL_WHISPER=1 JARVIS_WHISPER_WAV=clip.wav JARVIS_WHISPER_EXPECT="hora" \
  .venv/bin/python -m pytest tests/test_whisper_real.py -v -s -rs
# turno completo como el ESP32 (el texto y los ms de stt salen en el log del
# backend y en el evento turn_stage que ve la TUI)
python tools/sim_device.py --url http://127.0.0.1:8000 --token dev-jarvis-token --wav clip.wav
```

Sin `JARVIS_WHISPER_WAV` la prueba real envía un tono de 1 s y solo comprueba la ida y
vuelta (Whisper suele "oír" algo como `¡Gracias!` en un tono).

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

1. STT local: Whisper ya está detrás de `Transcriber`; falta comparar con Parakeet
   con los mismos audios en español y decidir midiendo (D4).
2. Enchufar Gentle Shell como agente y definir el esquema de acciones reales.
3. Probar el WAV hablado con el ESP32 y sintetizar la respuesta del agente cuando exista.
4. Que el daemon del fork reenvíe `esp32_state` a sus clientes (la TUI ya
   consumiría el evento).
5. Lo pendiente de los contratos: `409` ante un segundo turno, eventos
   `turn_stage` de `route`, `agent` y `tts` (`stt` ya sale), `turn_error` fuera de la
   etapa `stt`, y el resultado `{ok, detail}` de las acciones.
