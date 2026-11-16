# Backend e IA en la computadora

Actualización de diseño del **2026-11-11**, no implementación. Se conserva el nombre
`nube.md` para los enlaces existentes, pero la PC coordina el procesamiento; la nube
ya no es el centro obligatorio. Ver [arquitectura y fuentes](arquitectura.md).

## Pipeline previsto

Audio del ESP32 → transcripción en la PC → Gentle Shell en segundo plano → acciones
en la PC y síntesis de respuesta → audio al ESP32. La TUI muestra conversación y acciones.

| Etapa | Estado de la decisión |
|---|---|
| STT | Comparar Whisper con Parakeet ejecutado localmente en la primera fase. No hay selección final ni modalidad de despliegue de Whisper cerrada. |
| Agente | Gentle Shell como proceso sin interfaz propia. Gentle Dot sirve de referencia para proceso persistente y mensajes, no de integración verificada. |
| TTS | Pendiente de selección y pruebas de audio con el ESP32. |
| Transporte | Wi-Fi entre ESP32 y PC; protocolo, mensajes y autenticación pendientes. No hay RPC cerrado. |

## Alternativas del diseño anterior

Groq Whisper, OpenAI Whisper API y Deepgram permanecen como candidatos de STT, no
como proveedores elegidos. Para el modelo del agente, Groq/Llama, GPT-4o-mini y Gemini
son alternativas por evaluar. OpenAI TTS-1, Piper, ElevenLabs y edge-tts son candidatos
de voz, sin promesas de precio o latencia. No se garantiza que toda la cadena sea offline.

## Acuerdos previos a implementar

- Comparar transcripción con los mismos audios en español, registrando errores, latencia
  y recursos; decidir después de medir.
- Verificar cómo iniciar, mantener y comunicar Gentle Shell con backend y TUI.
- Definir audio de entrada/salida, transporte, errores y límites de espera. WAV/PCM,
  MP3, HTTP y WebSocket eran propuestas anteriores, no contratos aprobados.
- Delimitar permisos y catálogo de acciones de la PC; excluir domótica y periféricos.
- Mantener credenciales de proveedores fuera del firmware y definir autenticación.
- Medir costos y latencia según proveedores reales, incluyendo tareas largas del agente.

Los ejemplos anteriores de JSON, endpoints y tokens no eran contratos vigentes; los
que siguen sí lo son, en versión v0.

## Orquestador v0 en la PC — decisión del 2026-11-16

El orquestador corre en la computadora del usuario (FastAPI, puerto 8000) y el ESP32
lo alcanza por la LAN; no hay despliegue del orquestador en la nube. El esqueleto
vive en `backend/` y está probado con `curl`; los proveedores reales se integran
después de medirlos, siguiendo las decisiones pendientes de arriba.

| Endpoint | Entrada | Salida |
| --- | --- | --- |
| `POST /api/v1/converse` | WAV multipart + `X-Device-Token` | `reply_text`, `reply_audio_url`, `actions` |
| `POST /api/v1/heartbeat` | JSON de ciclo (cada 5 s) | `200` y reenvío `esp32_state` por WebSocket |
| `WS /api/v1/ws?token=…` | token del anfitrión | `esp32_snapshot`, `esp32_state`, `action` |
| `GET /api/v1/health` | — | estado y dispositivos conectados |

Marcadores de posición del esqueleto: STT, agente (Gentle Shell) y TTS —
`/api/v1/converse` responde con un beep generado en el arranque y una acción de
prueba hasta elegir y medir esos proveedores. Las acciones salen por el WebSocket
hacia el anfitrión; el ESP32 no las recibe.

Pruebas e instrucciones: [backend/README.md](../backend/README.md).
Siguiente paso: acordar integración con [TUI](tui.md) y [firmware](firmware.md).
