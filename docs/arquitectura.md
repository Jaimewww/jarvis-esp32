# Arquitectura del Sistema

## Visión general

Arquitectura **híbrida Edge + Cloud + Host**: el ESP32-S3 solo captura y reproduce audio; toda
la inteligencia vive en APIs de nube; y las **acciones se ejecutan en la computadora** mediante
un *host agent* con interfaz **TUI** (el asistente controla la PC, no periféricos externos).

```
┌─────────────────────────── ESP32-S3 (Edge) ────────────────────────────┐
│  INMP441 (I2S mic) ──► captura 16 kHz / 16-bit mono                    │
│  Botón Push-to-Talk o VAD ──► detecta fin de habla                     │
│  ──► HTTP POST audio (WAV/PCM) por WiFi ────────────────┐              │
│  ──► HTTP streaming respuesta ──► MAX98357A ──► Altavoz │              │
│  ──► Anillo WS2812B: azul/amarillo/verde/rojo (estado)  │              │
└───────────────────────────────── USB = solo alimentación┼──────────────┘
                                                          ▼
                                  ┌─────────── Backend en la nube ───────────┐
                                  │ 1. STT: Groq (Whisper large-v3)          │
                                  │ 2. LLM: llama-3.3-70b — prompt J.A.R.V.I.S.│
                                  │ 3. TTS: OpenAI TTS-1 (o Piper)           │
                                  │ 4. Devuelve: audio MP3 + JSON de acciones│
                                  └───────────────┬──────────────────────────┘
                                                  │ WebSocket (acciones + estado)
                                                  ▼
                        ┌─────────── PC del usuario (Host) ───────────┐
                        │  Host agent (Python):                       │
                        │   • ejecuta acciones: teclas multimedia,    │
                        │     abrir apps, volumen, screenshots        │
                        │   • TUI (Textual): transcripción de la      │
                        │     conversación, estado, log de acciones   │
                        └─────────────────────────────────────────────┘
```

El ESP32 se conecta a la PC **únicamente por USB para alimentación y debug serial**; el canal
de control del sistema viaja por la nube: backend → host agent (WebSocket).

## Flujo detallado (un turno de conversación)

1. **Escucha.** El ESP32 lee el micrófono INMP441 por I2S a 16 kHz, 16 bits, mono.
   Dos modos posibles:
   - *Push-to-Talk (recomendado para v1):* mantener apretado un botón = grabar; soltar = enviar.
   - *VAD (v2):* detección de voz por umbral de energía RMS para recortar silencios.
2. **Envío.** El fragmento se empaqueta en WAV y se sube por HTTPS al orquestador
   (ver `nube.md`).
3. **Procesamiento.** Whisper transcribe; el LLM interpreta la intención y redacta la
   respuesta; si es una orden, además devuelve un JSON de acción para la PC.
4. **Respuesta en voz.** El TTS genera MP3; el ESP32 lo descarga en streaming y lo reproduce
   por el MAX98357A.
5. **Acción en la PC.** El backend emite la acción por WebSocket al host agent, que la
   ejecuta en el sistema (ej. `media_key: play_pause`, `app: spotify`) y lo reporta en la TUI.
6. **Estado.** El anillo WS2812B refleja el ciclo completo:
   **azul = reposo, amarillo = pensando, verde = hablando, rojo = error.**

## Presupuesto de latencia (esperado: 1,5–3 s por turno)

| Etapa | Latencia típica |
|---|---|
| Grabación (dura mientras hablás) | 1–5 s (no cuenta como espera) |
| Subida del audio por WiFi | 100–400 ms |
| STT (Groq Whisper) | 200–500 ms |
| LLM (primera respuesta) | 300–800 ms |
| TTS (síntesis + descarga) | 400–1000 ms |
| Reproducción | empieza apenas llegan los primeros chunks |
| Ejecución en la PC (host agent) | < 100 ms (local) |
| **Espera total percibida** | **~1–3 s** |

Tácticas para reducirla:
- Streaming del TTS: reproducir mientras descarga (chunked HTTP), no esperar el archivo completo.
- Elegir modelo chico de LLM (`llama-3.1-8b-instant` en Groq) para conversación simple y
  escalar a modelo mayor solo si la intención lo pide.
- Keep-alive de conexión TLS al orquestador.
- Ejecutar la acción **antes** de que termine la respuesta hablada (la orden no debe esperar al audio).

## Decisiones de diseño clave

| Decisión | Elección | Motivo |
|---|---|---|
| Micro | ESP32-S3 con PSRAM (8 MB) | I2S + TLS + buffers de audio agotan la RAM sin PSRAM |
| Captura de voz | PTT en v1, VAD en v2 | PTT elimina falsos positivos y simplifica el firmware inicial |
| STT | Groq Whisper large-v3 | Latencia mínima, capa gratuita generosa |
| LLM | Groq llama-3.3-70b (opción GPT-4o-mini/Gemini) | Velocidad; System Prompt fijo de J.A.R.V.I.S. |
| TTS | OpenAI TTS-1 (o Piper self-hosted si se quiere 100 % libre) | Calidad/latencia razonable |
| Ejecución de órdenes | Host agent en la PC (Python) vía WebSocket | Robusto: el firmware no parsea lenguaje natural ni mueve GPIOs de control |
| Interfaz en la PC | TUI con Textual (terminal) | Ligera, se ve en cualquier terminal, estilo J.A.R.V.I.S. |
| Audio digital | I2S en ambos extremos (mic y amp) | Sin ruido PWM ni interferencias analógicas |
| IDE | Arduino IDE + ESP32 core 3.x | Menor fricción; ESP-IDF solo si hay que optimizar hilos FreeRTOS |
