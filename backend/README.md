# Orquestador J.A.R.V.I.S. v0 — corre en la PC

Esqueleto del contrato de transporte v0 (decisión del 2026-11-16, ver
`docs/arquitectura.md` y `docs/nube.md`). Corre en la computadora del usuario; el
ESP32 lo alcanza por la LAN.

## Qué está implementado

- `POST /api/v1/converse` — recibe el WAV del ESP32 (multipart), lo guarda y
  responde `reply_text`, `reply_audio_url` y `actions`. **Marcador de posición:**
  STT, agente (Gentle Shell) y TTS no están integrados; responde con un beep
  generado en el arranque y una acción de prueba.
- `POST /api/v1/heartbeat` — estado del ESP32 (`idle`/`recording`/`thinking`/
  `speaking` + RSSI e IP); lo reenvía a los clientes anfitriones como
  `esp32_state`.
- `WS /api/v1/ws?token=…` — canal del anfitrión: snapshot inicial
  (`esp32_snapshot`) y eventos `esp32_state` y `action`. Sin heartbeat durante
  15 s el dispositivo se marca `offline` y se notifica.
- `GET /api/v1/health` — prueba de vida y estado de los dispositivos.

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
