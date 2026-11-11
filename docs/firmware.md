# Plan de Firmware (ESP32-S3)

Entorno: **Arduino IDE + ESP32 board package v3.x** (soporte I2S moderno). ESP-IDF es una
alternativa solo si hay que afinar prioridades de FreeRTOS más adelante.

## Estructura propuesta

```
firmware/
├── firmware.ino          // setup()/loop(): orquestación de estados
├── config.h              // pinout, credenciales WiFi, URLs de la API
├── audio_capture.h/.cpp  // I2S RX: INMP441, VAD por RMS, WAV builder
├── audio_playback.h/.cpp // I2S TX: MAX98357A, streaming de MP3/WAV
├── cloud_client.h/.cpp   // HTTPS: sube WAV, recibe audio de respuesta
├── status_leds.h/.cpp    // WS2812B: máquina de estados de color
└── state_machine.h/.cpp  // IDLE → RECORDING → THINKING → SPEAKING → IDLE

> Nota: las órdenes (teclas, apps, volumen) NO se ejecutan en el ESP32: las ejecuta el
> host agent en la PC (ver `tui.md`). El firmware solo produce/consume audio y pinta el estado.
```

## Máquina de estados (núcleo del loop)

```
IDLE ──(botón presionado)──► RECORDING ──(botón soltado)──► THINKING
 ▲                                                            │
 │              (acción JSON)                                 ▼
 └────────── IDLE ◄──(fin de audio)── SPEAKING ◄──(audio listo)┘
 
THINKING con error ──► ERROR (anillo rojo + beep) ──► IDLE
```

El anillo WS2812B siempre refleja el estado: azul IDLE, amarillo THINKING,
verde SPEAKING, rojo ERROR. Es también la primera herramienta de debugging visual.

## Librerías

| Librería | Uso |
|---|---|
| `ESP_I2S` (incluida en core 3.x) | Driver I2S para mic y amp |
| `WiFi` + `WiFiClientSecure` | Conexión TLS al orquestador |
| `HTTPClient` | POST del WAV, GET/POST streaming del audio de respuesta |
| `ArduinoJson` (v7) | Parseo del JSON de acciones |
| `Adafruit_NeoPixel` | Anillo WS2812B |
| `ESP32-audioI2S` (schuhboo, opcional) | Decodificador MP3→I2S ya resuelto; alternativa a decodificar a mano |

## Puntos críticos de diseño

1. **PSRAM sí o sí.** Todos los buffers grandes (WAV capturado ~160 KB por 5 s a 16 kHz/16-bit,
   chunks de MP3 de entrada) van a `ps_malloc()`. Nunca `malloc()` del heap interno.
2. **Dos periféricos I2S.** El core 3.x permite dos instancias I2S (RX para el mic, TX para el
   amp). No compartir bus: mic y amp en pines separados (ver `materiales.md`).
3. **Streaming, no cargar todo.** El audio de respuesta se reproduce por chunks a medida que
   llega por HTTP (chunked transfer). Reduce latencia percibida ~0,5–1 s y memoria usada.
4. **El zumbido.** Picos de corriente del WiFi TX + audio por el mismo 5 V = buzz. Mitigar:
   condensador 1000 µF en la entrada del MAX98357A + 100 nF en cada módulo, y si persiste,
   alimentar el amp desde un regulador/fuente separada.
5. **TLS y memoria.** Una conexión TLS consume ~40 KB. Usar `WiFiClientSecure` con
   `setInsecure()` solo en prototipo; para la versión final, fijar el certificado raíz.
6. **Watchdog.** Toda operación de red dentro de `loop()` con timeouts; nunca bloquear sin
   `yield()` o el WDT reinicia la placa.
7. **Botón PTT con debounce** y detección de flanco (press/release), no polling simple.

## Configuración I2S de referencia

| Parámetro | Captura (mic) | Reproducción (amp) |
|---|---|---|
| Sample rate | 16000 Hz | 24000 Hz (TTS típico) o 44100 |
| Bits | 16 | 16 |
| Canales | Mono (LEFT) | Mono |
| DMA buffers | 8 × 256 muestras | 8 × 512 muestras |

## Protocolo con el backend (contrato con R4)

**Request:** `POST /api/v1/converse` con `multipart/form-data`:
- `audio`: WAV (16 kHz, 16-bit, mono)
- `device_id`: string

**Response:** headers + body JSON:
```json
{
  "reply_text": "Abriendo Spotify, señor.",
  "reply_audio_url": "https://.../tts/abc123.mp3",
  "actions": [ {"type": "app", "value": "open:spotify"} ]
}
```
El firmware descarga `reply_audio_url` y lo reproduce. El campo `actions` lo ignora:
el backend lo entrega en paralelo al host agent de la PC por WebSocket (ver `tui.md`).

## Soporte de memoria: qué vigilar

| Síntoma | Causa probable | Fix |
|---|---|---|
| `Guru Meditation Error: Core panicked` | Buffer grande en heap interno | Buffers a PSRAM |
| Reinicios al transmitir WiFi + reproducir | Brownout por picos de corriente | Condensadores / fuente separada |
| `I2S: alloc failed` | Fragmentación del heap | Buffers estáticos/PSRAM, evitar String |
| Audio con clics/cortes | DMA buffers insuficientes | Aumentar buffer count/size |
