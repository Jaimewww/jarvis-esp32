# Arquitectura del sistema

## Diseño vigente — 2026-11-11

El ESP32-S3 es el terminal de voz y la computadora es el centro de procesamiento.
Este diseño sustituye la arquitectura anterior centrada obligatoriamente en la nube;
todavía no está implementado ni validado en hardware.

| Equipo | Responsabilidades previstas |
|---|---|
| ESP32-S3 | Detectar «Jarvis», capturar INMP441 por I2S, detectar fin del habla, enviar audio por Wi-Fi, reproducir por MAX98357A y mostrar estado con WS2812. |
| Computadora | Recibir audio, transcribir, conversar con Gentle Shell, sintetizar respuesta, ejecutar acciones y mostrar conversación y registro en la TUI. |

## Un turno de conversación

1. WakeNet de ESP-SR detecta «Jarvis» en el ESP32-S3; el pulsador queda como respaldo.
2. Se captura la orden a 16 kHz hasta detectar fin del habla y se envía por Wi-Fi a la PC.
3. La PC transcribe; la primera fase compara Whisper y Parakeet local, sin ganador fijado.
4. Gentle Shell, como proceso en segundo plano sin interfaz propia, recibe el texto.
5. La PC ejecuta las acciones solicitadas (por ejemplo, abrir una aplicación o cambiar
   volumen), sintetiza la respuesta y devuelve el audio al ESP32 para reproducirlo.
6. Anillo y TUI reflejan reposo, escuchando, pensando, hablando y error. Los colores
   exactos y la sincronización quedan por acordar; deben indicar tareas largas en curso.

Gentle Dot es una referencia para mantener vivo un proceso de agente y conversar mediante
mensajes, no evidencia de una integración funcional. El informe no define RPC ni cierra
HTTP/WebSocket, esquemas de mensajes, autenticación o formato de audio de respuesta.
La PC puede recurrir a proveedores externos: no se promete funcionamiento totalmente offline.

## Alcance

El prototipo previsto incluye carcasa, voz, acciones en la computadora y TUI.
Se excluyen domótica u otros periféricos, operación sin PC encendida en la misma red,
batería, identificación del hablante y uso de varias personas a la vez. Alimentación USB.

## Riesgos y decisiones pendientes

- WakeNet y `wn9_jarvis_tts` tienen respaldo documental, no pruebas en esta placa.
  Espressif advierte que la generalización de modelos entrenados con TTS no está garantizada.
- Medir aciertos y activaciones falsas con voces del equipo y ruido de aula.
- Definir STT final, TTS, modelo del agente y contratos de comunicación antes de integrar.
- Medir tiempos de captura, transferencia, STT, agente, acciones y TTS por separado.
  Las estimaciones anteriores de 1–3 s no son una garantía ni una medición vigente.

## Procedencia de esta actualización

Fuente: informe «J.A.R.V.I.S. de escritorio», `body.md`, del 2026-11-11, secciones
Tema, Integrantes y roles, Alcance, Riesgos y Presupuesto; contrastado con
`research-notes.md` de la misma fecha. Las notas respaldan ESP-SR y el modelo, pero no
verifican Gentle Shell/Gentle Dot, LLM o TTS; no deben leerse como prueba de integración.

Referencias técnicas: [ESP-SR](https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/index.html),
[modelos WakeNet](https://github.com/espressif/esp-sr/blob/master/wakeword_list.md) y
[Gentle Dot](https://github.com/Gentleman-Programming/gentle-dot).

Siguiente paso: [plan de trabajo](planificacion.md) y [backend e IA](nube.md).
