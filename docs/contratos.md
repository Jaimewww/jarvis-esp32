# Contratos entre partes

Propuesta del **2026-11-18** para que dos personas trabajen en paralelo sin chocar: una
lleva `backend/` (camino de voz) y la otra `host/` (TUI) y el adaptador de Gentle Dot.
Estos cuatro contratos son la frontera entre las dos partes. Lo que no está aquí lo
decide quien es dueño de esa parte.

Cada punto indica si está **implementado** en el código o **pendiente**. Las reglas de
fondo están en [decisiones](decisiones.md).

| # | Contrato | Entre | Dónde vive |
|---|---|---|---|
| C1 | Dispositivo ↔ backend | Firmware y backend | `backend/app/main.py` |
| C2 | Backend → host | Backend y TUI | `WS /api/v1/ws` |
| C3 | Interfaz de agente | Motor de charla y adaptador de Gentle Dot | `backend/app/ports.py` |
| C4 | Acción del catálogo | Backend y quien ejecuta la acción | Evento `action` |

## C1 — Dispositivo ↔ backend

HTTP sobre Wi-Fi (D5), backend en el puerto 8000. Toda petición del dispositivo lleva
la cabecera `X-Device-Token`; su valor sale de `JARVIS_DEVICE_TOKEN` (por defecto
`dev-jarvis-token`, solo para desarrollo).

### `POST /api/v1/heartbeat`

Cuerpo JSON:

| Campo | Tipo | Por defecto | Estado |
|---|---|---|---|
| `device_id` | texto | `"esp32-s3"` | implementado |
| `state` | `idle`, `recording`, `thinking`, `speaking` o `error` | `"idle"` | implementado (`error` añadido en esta rama) |
| `rssi` | entero o `null` | `null` | implementado |
| `ip` | texto o `null` | `null` | implementado |

| Respuesta | Cuándo |
|---|---|
| `200 {"status":"ok"}` | Latido aceptado; se reenvía como `esp32_state` (C2) |
| `401 {"detail":"invalid device token"}` | Token incorrecto |
| `422` | Falta la cabecera del token, el JSON no es válido o el estado no está en la lista |

| Regla | Estado |
|---|---|
| Un latido cada 5 s | implementado en el backend: sin latido durante 15 s el dispositivo queda `online: false` |
| Un latido además en cada cambio de estado (D16) | pendiente (firmware); el simulador ya lo hace |

### `POST /api/v1/converse`

Cuerpo `multipart/form-data` con el campo `audio`: un WAV PCM de 16 kHz, mono, 16 bits
(D6).

Respuesta `200`:

| Campo | Tipo | Estado |
|---|---|---|
| `turn_id` | texto, identificador del turno | implementado |
| `reply_text` | texto | implementado (frase fija de prueba) |
| `reply_audio_url` | ruta relativa, p. ej. `/static/jarvis-reply.wav` | implementado (pitido de prueba) |
| `actions` | lista de acciones (C4) | implementado (una acción fija de prueba) |

El dispositivo descarga el audio con `GET <reply_audio_url>` (sin token). La respuesta
es WAV PCM de 16 kHz, mono, 16 bits.

| Respuesta | Cuándo | Estado |
|---|---|---|
| `200` | Turno completo | implementado |
| `401` | Token incorrecto | implementado |
| `422` | Falta el token, falta `audio` o el archivo está vacío | implementado |
| `409` | Ya hay un turno de voz en curso (D16) | **pendiente** |

### `GET /api/v1/health`

Sin token. Responde `{"status":"ok","devices":{<device_id>:{"state","online"}}}`.
Implementado.

## C2 — Backend → host

WebSocket `ws://<pc>:8000/api/v1/ws?token=<t>`, con `<t>` de `JARVIS_HOST_TOKEN` (por
defecto `dev-host-token`). Con un token incorrecto el backend cierra con código `4401`.
Los mensajes son JSON en **tramas binarias** (UTF-8). El canal es de solo lectura: lo
que envía el cliente se ignora.

**Regla:** el cliente ignora los tipos de evento y los campos que no conoce. Así el
backend puede añadir eventos sin romper la TUI.

### Eventos existentes

| `type` | Campos | Cuándo | Estado |
|---|---|---|---|
| `esp32_snapshot` | `devices`: lista de `{device_id, state, rssi, ip, online, ts}` | Al conectar | implementado |
| `esp32_state` | `device_id`, `state`, `rssi`, `ip`, `online`, `ts` | En cada latido y al pasar a `online: false` | implementado |
| `action` | `device_id`, `action` (C4), `ts` | Por cada acción del turno | implementado |

### Eventos de turno

Todos llevan `type`, `turn_id` (el mismo que devuelve `/converse`) y `ts` (segundos
Unix, decimal).

| `type` | Campos propios | Cuándo | Estado |
|---|---|---|---|
| `turn_started` | `device_id` | Al recibir el audio | implementado |
| `turn_stage` | `stage`: `stt`, `route`, `agent` o `tts`; `ms`; `text` opcional; `warning` opcional | Al terminar cada etapa | pendiente |
| `turn_done` | `lane`: `fast`, `chat` o `task`; `total_ms`; `reply_text` | Al terminar el turno | implementado (siempre `lane: "chat"` y la frase de prueba) |
| `turn_error` | `stage`, `message` | Si una etapa falla | pendiente |

`text` en `turn_stage` lleva lo transcrito (`stt`) o lo respondido (`agent`). `warning`
va solo en un `stt` lento (desde `JARVIS_STT_SLOW_MS`, 3000 ms por defecto): un texto
que dice que whisper-server parece correr en CPU; el cliente lo muestra como aviso. Un turno
termina con un solo `turn_done` o un solo `turn_error`.

## C3 — Interfaz de agente

Cada etapa del turno está detrás de un `Protocol` de Python en `backend/app/ports.py`
(D19). Implementado como interfaz; todavía ninguna implementación real.

```python
class Transcriber(Protocol):
    async def transcribe(self, wav: bytes) -> str: ...

class Agent(Protocol):
    def reply(self, text: str, *, turn_id: str) -> AsyncIterator[str]: ...
    async def abort(self) -> None: ...
    async def reset(self) -> None: ...

class Synthesizer(Protocol):
    async def synthesize(self, text: str) -> bytes: ...  # WAV 16 kHz mono 16 bits
```

| Método | Contrato |
|---|---|
| `Agent.reply` | Entrega la respuesta en trozos de texto (p. ej. una frase cada uno), para sintetizar la primera mientras sigue el resto (D17) |
| `Agent.abort` | Corta la respuesta en curso; no falla si no hay ninguna |
| `Agent.reset` | Olvida la conversación (D14: tras silencio o cierto número de turnos) |

| Implementación | Quién | Carril | Estado |
|---|---|---|---|
| Motor de charla | Parte de voz (`backend/`) | `chat` | pendiente; motor por elegir (D14) |
| Adaptador de Gentle Dot | Parte de host | `task` | implementado en `backend/app/agents/gentle_dot.py` (sin `reset`); probado con daemon falso y, opcional, con el real (`JARVIS_REAL_DAEMON=1`); sin conectar a `/converse` |
| `FakeTranscriber`, `FakeAgent`, `FakeSynthesizer` | Ya en `backend/app/fakes.py` | pruebas | implementado |

El adaptador de Gentle Dot vive en `backend/` porque el backend es quien lo llama (D2),
pero lo escribe y mantiene la parte de host, que es quien conoce el daemon.

## C4 — Acción del catálogo

Una acción es `{"type": <id del catálogo>, "value": <texto o null>}`; viaja en la lista
`actions` de `/converse` y en el evento `action` de C2. Ejemplo que ya envía el backend:
`{"type":"media_key","value":"play_pause"}`.

Su resultado es `{"ok": <bool>, "detail": <texto>}`.

| Punto | Estado |
|---|---|
| Forma `{type, value}` | implementado; `value` hoy es obligatorio, `null` pendiente |
| Resultado `{ok, detail}` | pendiente |
| Ejecución y quién la hace | pendiente |

**Catálogo inicial: propuesta, no decisión** (la lista está abierta en D8):

| `type` | `value` | Efecto | Herramienta (D8) |
|---|---|---|---|
| `media_key` | `play_pause`, `next`, `previous` | Multimedia | `playerctl` |
| `volume` | `up`, `down`, `mute` | Volumen | `wpctl` |
| `open_app` | id de la aplicación, p. ej. `firefox` | Abrir aplicación | `gtk-launch` |

## Dobles para avanzar sin la otra parte

| Doble | Reemplaza a | Uso |
|---|---|---|
| `backend/app/fakes.py` | STT, agente y TTS reales | Pruebas del backend y desarrollo de la TUI contra un turno completo |
| `tools/sim_device.py` | El ESP32 | Recorre `idle → recording → thinking → speaking → idle`, envía un WAV a `/converse`, descarga el audio y mide el tiempo |

Simulador, solo con la biblioteca estándar:

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000   # en otra terminal
python tools/sim_device.py --url http://127.0.0.1:8000 --token dev-jarvis-token
python tools/sim_device.py --wav orden.wav                 # con un audio propio
```

Sin `--wav` envía un tono de 1 s generado. El token por defecto sale de
`JARVIS_DEVICE_TOKEN`, igual que en el backend. Termina con código 1 y un mensaje
claro ante un error HTTP o de conexión.

Pruebas del backend: ver [backend/README.md](../backend/README.md#pruebas).

## Cómo cambiar un contrato

1. Primero se cambia este documento, en su propio pull request, y lo revisa la otra
   parte.
2. Después se cambia el código, en otro pull request que cita el anterior.
3. Un campo nuevo que el otro lado puede ignorar no rompe a nadie; quitar o renombrar
   un campo sí, y requiere acuerdo de ambos.
