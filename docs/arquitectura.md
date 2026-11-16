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

## Transporte por red — decisión del 2026-11-16

Topología elegida: el orquestador corre en la PC del usuario y el ESP32-S3 lo alcanza
por la LAN; la nube queda reducida a los proveedores externos (STT, agente, TTS) que
la PC llama. Esto cierra en versión v0 la pregunta abierta de transporte del informe:
protocolo, mensajes y autenticación quedan definidos, implementados como esqueleto en
`backend/` y verificables con `curl`.

- Dirección del orquestador: `http://<IP-LAN>:8000`, con nombre estable por mDNS
  (`jarvis-backend.local`) o reserva DHCP, para no depender de una IP que cambia.
- Autenticación: header `X-Device-Token` en toda petición del ESP32; el daemon y la
  TUI siguen el protocolo v1 de Gentle Dot con su propio token.
- Heartbeat: cada 5 s el ESP32 informa su ciclo — `idle`, `recording`, `thinking`,
  `speaking` — junto con RSSI e IP; es el mismo ciclo que pinta el anillo WS2812.
- Sin heartbeat durante 15 s el orquestador marca el dispositivo offline y lo
  notifica a los clientes anfitriones.

### Endpoints v0 (esqueleto en `backend/`)

| Endpoint | Método | Uso |
| --- | --- | --- |
| `/api/v1/converse` | POST | El ESP32 sube un WAV (multipart); responde texto, URL del audio de respuesta y acciones |
| `/api/v1/heartbeat` | POST | Presencia y ciclo del ESP32 (cada 5 s) |
| `/api/v1/ws` | WebSocket | Canal del anfitrión: snapshot, `esp32_state` y `action` |
| `/api/v1/health` | GET | Prueba de vida y dispositivos conectados |

Las acciones viajan al anfitrión por el WebSocket; el ESP32 no las recibe. La TUI
muestra el estado del terminal como evento `esp32_state` (ver [TUI](tui.md)).

## Procedencia de esta actualización

Fuente: informe «J.A.R.V.I.S. de escritorio», `body.md`, del 2026-11-11, secciones
Tema, Integrantes y roles, Alcance, Riesgos y Presupuesto; contrastado con
`research-notes.md` de la misma fecha. Las notas respaldan ESP-SR y el modelo, pero no
verifican Gentle Shell/Gentle Dot, LLM o TTS; no deben leerse como prueba de integración.

Referencias técnicas: [ESP-SR](https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/index.html),
[modelos WakeNet](https://github.com/espressif/esp-sr/blob/master/wakeword_list.md) y
[Gentle Dot](https://github.com/Gentleman-Programming/gentle-dot).

Siguiente paso: [plan de trabajo](planificacion.md) y [backend e IA](nube.md).
