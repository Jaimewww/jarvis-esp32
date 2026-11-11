# Stack de IA en la nube (Backend)

Arquitectura recomendada: un **orquestador propio** (un script Python con FastAPI/Flask
desplegado gratis en Render/Railway/Fly.io, o incluso local con tunnel) que encadena
STT → LLM → TTS. El ESP32 solo habla con el orquestador: una sola URL, una sola credencial.

> Para prototipo también funciona correr el orquestador en una laptop de la facultad y usar
> un túnel (ngrok / cloudflared) para que el ESP32 lo alcance desde el WiFi local.

## 1. STT — Transcripción

**Elegido: Groq `whisper-large-v3`** (alternativas: OpenAI Whisper API, Deepgram).

```
POST https://api.groq.com/openai/v1/audio/transcriptions
Authorization: Bearer $GROQ_API_KEY
file = audio.wav (16 kHz, 16-bit, mono)
model = whisper-large-v3
language = es
```

- Latencia típica: 200–500 ms. Capa gratuita generosa.
- `language=es` evita autodetección lenta y errores con acento rioplatense.

## 2. LLM — El cerebro

**Elegido: Groq `llama-3.3-70b-versatile`** (rápido y barato). Opciones: `llama-3.1-8b-instant`
para conversación simple, GPT-4o-mini o Gemini Flash si se quiere más criterio.

### System Prompt de J.A.R.V.I.S. (v1 — ajustar en iteraciones)

```
Eres J.A.R.V.I.S., el asistente personal de un usuario. Personalidad: sarcástico pero
respetuoso, extremadamente eficiente y servicial, con humor británico seco. Respondes en
español rioplatense. Eres conciso: máximo 2 oraciones por respuesta hablada, salvo que te
pidan detalles. Si el usuario te da una orden sobre el hogar (luces), debes incluir la
acción en el campo JSON. Nunca menciones que eres un modelo de lenguaje.
```

### Contrato de salida: texto + intención estructurada

Forzar salida con **function calling / JSON mode** para que siempre devuelva:

```json
{
  "speech": "Abriendo Spotify, señor.",
  "actions": [
    {"type": "app", "value": "open:spotify"}
  ]
}
```

Catálogo de acciones sobre la computadora se inyecta en el system prompt (lista dinámica):
`media_key` (play_pause/next/prev), `volume`, `app` (open:spotify, open:chrome, …),
`screenshot` (v2). Las ejecuta el **host agent** de la PC (ver `tui.md`), no el ESP32.
El backend entrega las acciones al host agent por WebSocket en paralelo al audio.

## 3. TTS — Síntesis de voz

| Opción | Calidad | Latencia | Costo | Nota |
|---|---|---|---|---|
| **OpenAI TTS-1** (`alloy`/`onyx`) | Alta | 400–800 ms | ~$15/1 M chars | Elegida por defecto; voz `onyx` suena más "mayordomo" |
| ElevenLabs | Excelente | 500–1000 ms | Capa gratuita chica | Mejor clonación de voz |
| **Piper** (self-hosted) | Buena | <300 ms | Gratis | 100 % libre; ideal si se quiere cero dependencia comercial |
| edge-tts (MS Edge) | Alta | variable | Gratis (no oficial) | Riesgo de que lo corten; solo prototipo |

Formato de salida: MP3 24 kHz — el ESP32 lo decodifica y reproduce por I2S.

## 4. Orquestador (esqueleto)

```python
# server.py — FastAPI (pseudoestructura)
@app.post("/api/v1/converse")
async def converse(device_id: str, audio: UploadFile):
    text = await groq_stt(audio)            # 1. transcribe
    result = await llm_decide(text)         # 2. LLM con prompt JARVIS → JSON
    audio_url = await tts(result.speech)    # 3. sintetiza y guarda en /static
    await ws_push_host(result.actions)      # 4. acciones → host agent de la PC
    return {"reply_text": result.speech,
            "reply_audio_url": audio_url,
            "actions": result.actions}
```

El host agent se conecta como cliente WebSocket al backend y recibe allí las acciones + el
estado del ciclo (IDLE/THINKING/SPEAKING) para sincronizar la TUI con el anillo LED.

- Guardar el MP3 en un bucket/static y devolver URL: permite que el ESP32 lo streamee.
- Auth simple: header `X-Device-Token` (inventado por el equipo) para que no cualquiera hable
  con las luces de tu casa.

## 5. Seguridad y costos

- **API keys solo en el orquestador**, nunca en el firmware del ESP32 (se puede extraer del
  binario). El ESP32 conoce únicamente la URL del orquestador y su device token.
- Rate limit en el orquestador (ej. 10 req/min por device) para que un bug del firmware no
  queme la cuota.
- Costo estimado por conversación (5 s de audio): < $0,002 con Groq + TTS-1. La capa gratuita
  de Groq alcanza para todo el desarrollo.

## 6. Prompts/protocolo a definir en Fase 2 (entregables de R4)

- [ ] `prompts/jarvis_system.md` — prompt final con catálogo de dispositivos.
- [ ] `prompts/acciones_pc.json` — schema de acciones sobre la PC (contrato con el host agent).
- [ ] Colección de Postman/curl con casos de prueba: orden de PC (media key, app), pregunta general, error.
