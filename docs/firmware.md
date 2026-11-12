# Plan de firmware — ESP32-S3

Diseño del **2026-11-11**, aún no implementado. El firmware detecta «Jarvis», captura y
reproduce audio, se comunica por Wi-Fi con la PC y controla el anillo. Las acciones y el
procesamiento STT/agente/TTS corresponden a la computadora.

## Activación y estados previstos

Reposo → «Jarvis» detectado → escuchando/capturando → fin del habla → pensando
→ audio de respuesta → hablando → reposo. Un fallo debe mostrar error y permitir recuperación.
El pulsador es respaldo, no la activación principal; incluir debounce al implementarlo.

WakeNet de ESP-SR y el modelo `wn9_jarvis_tts` son la elección de diseño investigada.
La documentación describe entrada a 16 kHz, mono, 16 bits. Esto no prueba compatibilidad
con el montaje ni calidad con voces reales: validar placa, modelo, memoria y ruido de aula.
La detección de fin del habla pertenece al alcance actual, no a una futura v2 opcional.

## Capas previstas en `firmware/`

| Capa | Trabajo pendiente |
|---|---|
| Audio y activación | INMP441 por I2S, adaptación del audio, WakeNet y detección de fin del habla. |
| Red | Enviar la orden a 16 kHz por Wi-Fi a la PC y recibir audio; contrato por definir. |
| Reproducción | MAX98357A por I2S; acordar formato, frecuencia y buffering de la respuesta. |
| Estado | Anillo WS2812: reposo, escuchando, pensando, hablando y error. |

Arduino IDE/core 3.x y ESP-IDF eran opciones del diseño anterior. Evaluar la integración
con ESP-SR antes de cerrar entorno y librerías; ESP-IDF no queda limitado a optimización.

## Validaciones técnicas necesarias

- Dimensionar buffers y PSRAM de la N16R8; vigilar memoria, DMA y reinicios.
- Probar ambos periféricos I2S y ajustar el [pinout propuesto](materiales.md).
- Medir ruido y alimentación con Wi-Fi y audio simultáneos; comprobar filtrado.
- Definir timeouts y recuperación de red sin bloquear indefinidamente el dispositivo.
- Acordar transporte y autenticación con [backend](nube.md). El antiguo endpoint y
  JSON de acciones no son contratos vigentes; el ESP32 no ejecuta acciones de la PC.
- Validar reproducción antes de fijar MP3/WAV, frecuencia de salida o streaming.

Ver [pruebas](pruebas.md) para la evidencia que deberá obtenerse en hardware.
