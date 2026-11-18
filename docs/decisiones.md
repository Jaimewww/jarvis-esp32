# Decisiones de arquitectura

Registro de las decisiones de base tomadas el **2026-11-18**. Cada una indica qué se
decidió, por qué y qué se descartó. Ninguna está validada en hardware todavía: son las
reglas con las que se construye, no resultados medidos.

## Resumen

| # | Tema | Decisión |
|---|---|---|
| D1 | Relación con Gentle Dot | Dependencia externa detrás de un adaptador propio |
| D2 | Topología en la PC | El backend es la pasarela de voz y cliente del daemon |
| D3 | Framework del firmware | ESP-IDF |
| D4 | STT y TTS | Locales en la PC |
| D5 | Enlace ESP32 ↔ PC | Wi-Fi, con el contrato HTTP v0 |
| D6 | Formato de audio | PCM 16 kHz, mono, 16 bits, en WAV |
| D7 | Fin del habla | Se detecta en el ESP32 |
| D8 | Acciones en la PC | Lista cerrada, sin confirmación |
| D9 | Estado del ESP32 en la TUI | La TUI se conecta también al backend |
| D10 | Primer hito | Turno completo desde la PC, sin hardware |
| D11 | Estructura del repositorio | `firmware/`, `backend/`, `host/`, `cad/`, `docs/` |

## D1 — Gentle Dot es una dependencia externa

Jarvis vive en este repositorio y usa el daemon de Gentle Dot (fork
`Jaimewww/gentle-dot`, rama `feat/linux-computer-use`) como agente, sin construir sobre
su código.

- **Fork mínimo y fijado a un commit.** En el fork solo va lo que no se puede hacer
  desde afuera. La lógica de Jarvis no entra ahí.
- **Adaptador en el backend.** El backend habla con «un agente» por una interfaz
  propia; Gentle Dot es una implementación. Si el fork falla, se reemplaza por una
  llamada directa a un modelo sin tocar el resto.

**Por qué:** el protocolo v1 se tomó del código del fork, no de un contrato acordado, y
un cambio allí rompe a sus clientes. El adaptador limita ese riesgo a un solo módulo.

**Descartado:** construir Jarvis dentro del fork (se hereda una aplicación Tauri completa
y el trabajo propio queda diluido) y prescindir de Gentle Dot (se rehace el agente).

## D2 — El backend es la pasarela de voz

`POST /api/v1/converse` hace el turno completo: transcribe el audio, envía el texto al
agente mediante el adaptador de D1, espera la respuesta, la sintetiza y devuelve el audio.

**Por qué:** hoy el backend (`:8000`) y el daemon (`:4317`) no se comunican, y nadie
entrega al agente el texto transcrito. Poner la unión en el backend la deja en este
repositorio, sin depender de cambios en el fork.

**Descartado:** que el daemon sea el centro y reciba el audio o reenvíe eventos del
ESP32, porque obliga a modificar el fork.

## D3 — Firmware con ESP-IDF

**Por qué:** ESP-SR y WakeNet son componentes de ESP-IDF, y la activación por voz es la
parte de mayor riesgo técnico. ESP-IDF da además control directo de I2S y de la PSRAM.

**Descartado:** Arduino core 3.x, por su soporte limitado de activación por voz.

**Pendiente:** validar WakeNet con el modelo de «Jarvis» en la placa real. El pulsador
sigue como respaldo.

## D4 — STT y TTS locales

Transcripción y síntesis corren en la PC, sin servicios externos en el camino de voz.

**Por qué:** la demostración no depende de internet ni de cuentas o límites de terceros.

**Pendiente:** elegir el modelo concreto. Para STT se mantiene la comparación entre
Whisper y Parakeet, ambos locales, con los mismos audios en español. Para TTS, Piper es
el primer candidato. Los servicios en línea quedan solo como referencia de comparación.

## D5 — Wi-Fi con el contrato HTTP v0

El ESP32 se comunica con el backend por Wi-Fi con los endpoints ya definidos
(`heartbeat` y `converse`). El firmware separa el mensaje del medio que lo transporta,
para poder añadir USB sin reescribir la lógica.

**Por qué:** el contrato v0 ya existe y está implementado en el backend. Se pasa a
transmisión por trozos solo si la latencia medida lo exige.

**Descartado:** Bluetooth, porque el ESP32-S3 solo tiene Bluetooth LE y no los perfiles
de audio del Bluetooth clásico. USB queda como alternativa viable, no como principal:
fue una sugerencia del docente, no un requisito.

**Riesgo:** las redes de la universidad pueden aislar a los clientes entre sí. La
demostración debe usar un router o punto de acceso propio.

## D6 — Formato de audio

PCM a 16 kHz, mono, 16 bits, en contenedor WAV, tanto en la orden como en la respuesta.
No se comprime mientras una medición no lo justifique.

## D7 — Fin del habla en el ESP32

El ESP32 detecta el final de la orden con el detector de voz de ESP-SR y solo entonces
envía el audio, para no subir silencio.

## D8 — Acciones: lista cerrada

Jarvis solo ejecuta acciones de bajo riesgo definidas de antemano (multimedia, volumen,
abrir aplicaciones). Todo lo demás se rechaza. No hay confirmación en pantalla ni por voz.

**Por qué:** mantiene el uso sin manos y acota lo que una transcripción errónea puede
provocar.

**Pendiente:** definir el catálogo y con qué se implementa. La propuesta es usar
herramientas que no dependen del escritorio (`playerctl`, `wpctl`, `gtk-launch`), que
funcionan en cualquier distribución. El control por teclas del fork (`grim`, `wtype`)
depende del compositor de Wayland y quedaría como extra.

## D9 — La TUI se conecta también al backend

Además de su conexión al daemon, la TUI abre el WebSocket del backend
(`/api/v1/ws`) para mostrar el estado del ESP32 y las acciones.

**Por qué:** evita esperar a que el fork reenvíe el evento `esp32_state`, que su
protocolo v1 no incluye.

## D10 — Primer hito: un turno completo desde la PC

Un script envía un WAV grabado a `/converse` y obtiene audio real: STT, agente y TTS
funcionando de punta a punta, con tiempos por etapa. El firmware avanza en paralelo y
se integra contra un backend que ya responde de verdad.

## D11 — Estructura del repositorio

| Carpeta | Contenido | Estado |
|---|---|---|
| `firmware/` | Proyecto ESP-IDF del ESP32-S3 | No existe |
| `backend/` | Pasarela de voz (FastAPI) | Contrato v0 con respuestas de prueba |
| `host/` | TUI | Cliente del daemon; falta la conexión al backend |
| `cad/` | Modelos de la caja | No existe |
| `docs/` | Diseño, plan y pruebas | Vigente |

Cada carpeta lleva su README y sus pruebas.

## Decisiones abiertas

- **PC de la demostración:** equipo, escritorio y si tiene GPU NVIDIA. Condiciona el
  tamaño del modelo de STT y qué acciones de D8 funcionan.
- **Modelos concretos** de STT y TTS (D4) y modelo del agente.
- **Catálogo de acciones** y su implementación (D8).
- **Autenticación:** los tokens actuales son valores de desarrollo.
- **Pruebas e integración continua:** no hay ninguna todavía.
