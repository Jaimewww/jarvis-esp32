# Decisiones de arquitectura

Registro de las decisiones de base tomadas el **2026-11-18**: D1–D12 fijan la
arquitectura, D13–D19 el comportamiento del software y D20–D21 el uso de Gentle Dot
y el reparto del trabajo. Cada una indica qué se
decidió, por qué y qué se descartó. Ninguna está validada en hardware todavía: son las
reglas con las que se construye, no resultados medidos.

Los contratos que derivan de estas decisiones están en [contratos](contratos.md).

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
| D8 | Acciones en la PC | Catálogo sin confirmación; lo demás va al agente |
| D9 | Estado del ESP32 en la TUI | La TUI se conecta también al backend |
| D10 | Primer hito | Turno completo desde la PC, sin hardware |
| D11 | Estructura del repositorio | `firmware/`, `backend/`, `host/`, `cad/`, `docs/` |
| D12 | Plataforma de la PC | Arch Linux con niri (Wayland) |
| D13 | Camino rápido | Las órdenes del catálogo no pasan por el agente |
| D14 | Carriles | Rápido, charla de voz y tarea larga, con dos motores |
| D15 | Interrupción | Con la voz, por etapas; pulsador como respaldo |
| D16 | Estados | Una máquina de estados por eventos, con tiempos máximos |
| D17 | Latencia | Meta de 1,5 s en acciones y 4 s en preguntas |
| D18 | Placa | ESP32-S3 N16R8, por duplicado |
| D19 | Convenciones de software | Interfaces por etapa, pruebas, CI y configuración |
| D20 | Control de la PC por el agente | Pista S2, por etapas y con resguardos propios antes de cada etapa |
| D21 | Reparto del trabajo | Dos pistas en la PC, con un responsable por carpeta |

## D1 — Gentle Dot es una dependencia externa

Jarvis vive en este repositorio y usa el daemon de Gentle Dot como agente, sin
construir sobre su código.

- **Daemon original, sin modificar y fijado a una versión.** Corre en segundo plano
  como servicio del sistema, sin la aplicación de escritorio. El proyecto original no
  trae un archivo de servicio; hay que escribirlo.
- **Adaptador en el backend.** El backend habla con «un agente» por una interfaz
  propia; Gentle Dot es una implementación. Si falla, se reemplaza por una llamada
  directa a un modelo sin tocar el resto.

**Por qué:** el protocolo v1 se tomó del código de Gentle Dot, no de un contrato
acordado, y un cambio allí rompe a sus clientes. El adaptador limita ese riesgo a un
solo módulo, y usar el daemon sin modificar evita mantener un fork.

**Cambio:** la primera versión de esta decisión usaba el fork `Jaimewww/gentle-dot`,
rama `feat/linux-computer-use`. Dejó de ser una dependencia; queda como referencia
para D20.

**Descartado:** construir Jarvis dentro del fork o recompilar la aplicación completa
(el soporte de Windows del proyecto original supera las 2000 líneas y hay que
mantenerlo contra sus cambios) y prescindir de Gentle Dot (se rehace el agente).

**Sin verificar:** el arranque del daemon como servicio se leyó en el código
(`packages/daemon/src/cli.ts`, Node 24 o superior); no se probó.

## D2 — El backend es la pasarela de voz

`POST /api/v1/converse` hace el turno completo: transcribe el audio, envía el texto al
agente mediante el adaptador de D1, espera la respuesta, la sintetiza y devuelve el audio.

**Por qué:** hoy el backend (`:8000`) y el daemon (`:4317`) no se comunican, y nadie
entrega al agente el texto transcrito. Poner la unión en el backend la deja en este
repositorio, sin depender de cambios en Gentle Dot.

**Descartado:** que el daemon sea el centro y reciba el audio o reenvíe eventos del
ESP32, porque obliga a modificar Gentle Dot.

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

## D8 — Acciones: catálogo y agente

El catálogo reúne las acciones de bajo riesgo definidas de antemano (multimedia,
volumen, abrir aplicaciones). Esas se ejecutan sin confirmación.

Un pedido fuera del catálogo va al agente, limitado por los resguardos de Gentle Dot.
Si el agente pide aprobación por el protocolo durante un turno de voz, Jarvis la niega
y lo dice en voz alta; la acción se puede pedir después desde la TUI.

**Por qué:** el catálogo acota lo que una transcripción errónea puede provocar sin
preguntar, y negar las aprobaciones evita turnos colgados y acciones riesgosas por voz.

**Cambio:** la primera versión de esta decisión rechazaba todo lo que estuviera fuera
del catálogo. Se abrió al agente para poder usar aplicaciones aún no probadas.

**Resguardos de Gentle Dot**, leídos en el código del proyecto original (`main`,
`f97d56d`), no en la rama del fork:

| Resguardo | Comportamiento |
|---|---|
| Permiso | Diálogo nativo; dura 30 minutos y solo se guarda en memoria |
| Parada de emergencia | `Alt+Shift+Esc`; termina la sesión y descarta las acciones en curso |
| Lista de bloqueo | Solo aplicaciones: gestores de contraseñas, ajustes del sistema y diálogos de seguridad |
| Límite | 10 acciones por segundo |
| Confirmación | Diálogo nativo ante acciones con palabras como enviar, pagar, comprar, eliminar o transferir |

**Límites que esto impone:**

- Las confirmaciones del control de la PC son ventanas en pantalla, no mensajes del
  protocolo. El backend no puede negarlas ni anunciarlas; alguien debe responderlas.
- El permiso vence a los 30 minutos, la misma duración que la prueba de estabilidad.
- El proyecto original no tiene control de la PC en Linux, así que en la demostración
  estos resguardos no intervienen: el agente no ve la pantalla ni maneja el teclado o
  el cursor (D20). Sigue pudiendo ejecutar órdenes del sistema.

**Implementación:** el catálogo se adapta a la plataforma de D12. La base son
`playerctl` (multimedia), `wpctl` (volumen) y `gtk-launch` (aplicaciones), que no
dependen del compositor.

**Pendiente:** la lista concreta de acciones.

## D9 — La TUI se conecta también al backend

Además de su conexión al daemon, la TUI abre el WebSocket del backend
(`/api/v1/ws`) para mostrar el estado del ESP32 y las acciones.

**Por qué:** evita esperar a que el daemon reenvíe el evento `esp32_state`, que su
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

## D12 — Plataforma: Arch Linux con niri

La demostración corre en dos equipos del grupo, ambos con Arch Linux y niri sobre
Wayland. Es la única plataforma que se prueba y se garantiza.

**Por qué:** acotar la plataforma evita cubrir escritorios que nadie del equipo usa. El
backend, el STT, el TTS y la TUI son Python y deberían correr en otras distribuciones,
pero eso no se verifica.

**GPU:** los dos equipos tienen tarjeta NVIDIA (uno, una RTX 4050; el modelo del otro no
está anotado), así que el STT puede usar aceleración en ambos.

## D13 — Camino rápido

El backend reconoce las órdenes del catálogo en el texto transcrito y las ejecuta
directamente. Solo lo que no reconoce llega al agente.

**Por qué:** el agente es la etapa más lenta y menos predecible del turno. Una orden
conocida no necesita razonamiento.

## D14 — Tres carriles

| Carril | Qué atiende | Contexto |
|---|---|---|
| Rápido | Órdenes del catálogo | Ninguno |
| Charla de voz | Preguntas cortas y seguimientos | Agente liviano propio del backend, con instrucciones de responder corto; su conversación se reinicia tras un tiempo de silencio o un número de turnos |
| Tarea larga | Lo que requiere explorar, como una aplicación no probada | Daemon de Gentle Dot, una tarea a la vez |

La tarea larga corre en segundo plano: Jarvis confirma que empezó, sigue disponible y
avisa al terminar. A la charla solo vuelve el resultado, en una frase.

**Por qué:** el agente tarda más cuanto más contexto acumula, y lo que más pesa son
las capturas de pantalla y los resultados de herramientas. Separarlos mantiene liviana
la charla. Una acción que se prueba y funciona pasa al catálogo y deja de usar el agente.

**Dos motores:** la charla y la tarea larga usan agentes distintos, ambos detrás de la
interfaz de D1. El daemon tiene un solo agente y una sola conversación activa para
todos sus clientes: no trabaja en paralelo, cambiar de conversación aborta el turno en
curso y sus instrucciones son globales. Por eso no puede atender la charla mientras
ejecuta una tarea.

El daemon ya limita el peso de las capturas: en cada llamada al modelo conserva solo
la última y compacta el contexto por su cuenta.

**Pendiente:** elegir el motor de la charla midiendo las alternativas en el primer
hito (un proceso propio de Gentle Shell, un modelo local u otro), y definir qué pasa
con un segundo pedido de tarea larga mientras hay una en curso.

**Descartado:** todo en el daemon en secuencia (se pierde el segundo plano) y dos
instancias del daemon (no se verificó que puedan convivir).

## D15 — Interrupción con la voz, por etapas

Decir «Jarvis» corta el turno en curso. Se construye en este orden:

1. Con el pulsador, que ya era el respaldo de la activación.
2. Con la voz mientras Jarvis piensa. El parlante está en silencio, así que no hay eco.
3. Con la voz mientras Jarvis habla. Requiere cancelación de eco con una referencia por
   software, porque el MAX98357A no devuelve la señal reproducida.

**Riesgo:** la etapa 3 puede provocar activaciones falsas con un solo micrófono. Se
conserva solo si pasa una prueba de activaciones falsas; si no, queda la etapa 2. La
caja debe separar el micrófono del parlante.

## D16 — Estados

- **Una máquina de estados en el firmware**, que avanza solo por eventos: reposo →
  escuchando → pensando → hablando → reposo, más error.
- **Todo estado distinto de reposo tiene un tiempo máximo** y vuelve solo a reposo.
- **El estado se informa al cambiar**, por el mismo `heartbeat`. El envío periódico
  queda para saber si el dispositivo sigue activo.
- **Un turno de voz a la vez.** El backend rechaza un segundo pedido mientras hay uno
  en curso. Las tareas largas de D14 no cuentan como turno.
- **Un identificador por turno**, presente en cada registro, evento y medición.
- **El backend no guarda conversación.** El contexto vive en el agente; el backend
  mantiene en memoria los dispositivos y el turno en curso, y escribe un registro de
  turnos en disco.
- **Cada falla tiene salida audible:** un audio de error pregrabado y el anillo en
  estado de error. Nunca silencio.

**Pendiente:** añadir `error` a los estados que acepta el backend, que hoy son `idle`,
`recording`, `thinking` y `speaking`.

## D17 — Latencia

Meta, medida desde el fin del habla hasta que empieza a sonar la respuesta: **1,5 s en
acciones del catálogo y 4 s en preguntas**. Las tareas largas no tienen meta; se mide
la confirmación inicial.

Se mide antes de optimizar: cada turno registra el tiempo de cada etapa. Las mejoras se
aplican en este orden:

1. Modelos de STT y TTS cargados una sola vez, al arrancar.
2. Camino rápido (D13).
3. Respuesta por frases: se sintetiza la primera frase mientras el agente sigue.
4. Respuesta percibida: luz y sonido corto al detectar «Jarvis», y una frase pregrabada
   si el agente tarda.

El audio en transmisión continua queda fuera hasta que una medición lo justifique.

## D18 — Placa: ESP32-S3 N16R8

Se mantiene el ESP32-S3. Es la plataforma principal de ESP-SR, ejecuta WakeNet9 con
aceleración e incluye Wi-Fi.

- **Variante N16R8** (16 MB de flash, 8 MB de PSRAM). Los modelos de voz necesitan la PSRAM.
- **Dos placas y dos micrófonos**, para no detener las pruebas si uno se daña.
- **Con dos puertos USB** (UART y USB nativo).
- **Sin usar los GPIO 35, 36 y 37**, que en esta variante ocupa la PSRAM.

**Descartado:** ESP32-P4 (más potente, pero sin Wi-Fi propio y más caro) y ESP32
clásico, C3, C5 y C6 (solo ejecutan modelos de activación reducidos).

**Pendiente:** actualizar el presupuesto de [materiales](materiales.md) con las
unidades duplicadas.

## D19 — Convenciones de software

- **Cada etapa detrás de una interfaz** (STT, agente, TTS, acciones), para comparar
  modelos y probar con dobles.
- **Pruebas de contrato** de `/converse` con etapas falsas, y un conjunto de audios en
  español para comparar STT.
- **Integración continua mínima:** formato, tipos y pruebas en cada envío.
- **Configuración por variables de entorno**, con tokens generados en el primer
  arranque en lugar de valores por defecto.
- **Un solo comando** para iniciar el backend y el daemon.

## D20 — Control de la PC por el agente: pista S2, por etapas

El control completo de la PC por el agente (abrir aplicaciones, teclado, capturas de
pantalla y cursor) es responsabilidad de la pista S2 (D21). Se hace por etapas, de
menor a mayor riesgo, y cada etapa se prueba antes de pasar a la siguiente:

| Etapa | Qué | Base |
|---|---|---|
| a | Abrir aplicaciones y órdenes del sistema desde el agente | El servicio del daemon con acceso a la sesión gráfica (`WAYLAND_DISPLAY`) |
| b | Teclado y capturas de pantalla | `wtype` y `grim`, como la rama `feat/linux-computer-use` del fork |
| c | Cursor | Dispositivo virtual de Wayland con permisos propios |

Los resguardos van antes que el control: permiso con vencimiento, parada de
emergencia, lista de bloqueo y confirmación ante acciones irreversibles. Las etapas b y
c no se habilitan sin ellos.

**Diagnóstico de la etapa a (2026-11-20):** el agente ya ejecuta órdenes del sistema,
pero el servicio arrancó con el sistema, antes de que la sesión gráfica exportara
`WAYLAND_DISPLAY` y `DISPLAY`, y Brave falló con `Missing X server or $DISPLAY`.
Reiniciado el servicio dentro de la sesión, `brave` abrió una ventana nueva.

**Cambio:** la primera versión dejaba este control fuera de la demostración y para el
final. Se adelantó a pedido de la pista S2, que lo toma como funcionalidad propia.

**Cómo se haría:** un programa propio arranca el daemon y ocupa el lugar de la
aplicación de escritorio para registrarle un control de la PC para Linux. El daemon
solo acepta ese registro por el canal privado de quien lo arranca; por el WebSocket lo
rechaza a propósito. Es un añadido sobre el servicio de D1, no un reemplazo.

**Riesgos:**

- El mecanismo esquiva una protección deliberada del proyecto original y puede
  romperse si este la cambia. No se probó.
- El daemon confía en que el control de la PC se protege solo. El permiso, la lista de
  bloqueo y las confirmaciones habría que escribirlos.
- Mover el cursor en Wayland requiere un dispositivo virtual con permisos propios.

**Frontera con el catálogo (D8, D13):** las órdenes de bajo riesgo sin confirmación
(abrir una aplicación conocida, volumen, multimedia) siguen siendo el catálogo y el
camino rápido de la pista de voz; el control del agente cubre lo que el catálogo no.

## D21 — Reparto del trabajo

Programan dos personas, con los roles S1 y S2 del [plan](planificacion.md). Cada
carpeta tiene un responsable, y las fronteras son los
[contratos](contratos.md).

| Pista | Rol | Incluye | Carpeta |
|---|---|---|---|
| Voz | S1 | STT, TTS, motor de la charla, camino rápido y catálogo, registro de turnos | `backend/` |
| Agente y pantalla | S2 | TUI y su conexión al backend, servicio del daemon, adaptador de tareas largas, control de la PC por el agente (D20) | `host/` |
| Dispositivo | Por asignar cuando llegue la placa | Activación, captura, reproducción, luces, máquina de estados | `firmware/` |

- **Ramas y solicitudes de fusión** en lugar de enviar directo a `main`.
- **Un cambio de contrato va primero al documento**, en su propia solicitud.
- **Nadie espera a nadie:** el simulador de dispositivo y las etapas falsas permiten
  avanzar sin la placa y sin el agente.

El adaptador de tareas largas vive en `backend/` pero lo escribe la pista de agente,
contra la interfaz de agente del contrato C3.

## D22 — Plan de latencia

Se adopta el plan de [latencia-propuestas.md](latencia-propuestas.md), en este orden:
medir, camino rápido (P1), modelo según el pedido (P3), respuesta percibida y por
frases (P4), menos consultas en las tareas de la PC (P2) y medir IA local (P5).

- **El camino rápido vive en el backend** (D13, pista S1), antes del agente, así lo
  aprovechan el ESP32 y la TUI.
- **Catálogo inicial: lo básico.** Primero se define y se prueba qué órdenes entran;
  la lista se acuerda con S1 antes de programarla.
- **Tareas de la PC:** herramientas compuestas (`pc_write_in`, `pc_run`), sin captura
  de pantalla por defecto (solo si algo falló o se pide) y confirmación según la acción,
  no según las palabras del pedido. La confirmación va en su propia solicitud, con
  pruebas de escritorio y revisión, porque es un resguardo de D20.
- **IA local:** se mide con el mismo juego de pedidos (`python -m jarvis.bench`); adoptarla
  es otra decisión.

**Por qué:** la consulta al modelo (2,6-5,6 s cada una) domina el turno; STT tarda
0,2-0,3 s en GPU (y unos 15 s si cae a CPU, por eso se avisa). Metas previstas: orden del
catálogo de ~4,9 s a ~0,5-1 s; tarea de la PC de ~48 s a ~10-15 s.

## D23 — Confirmación según la acción, no según las palabras

El ayudante `jarvis-input` pide confirmación en la TUI antes de una acción irreversible
según **la acción**, nunca según las palabras del texto escrito o de la intención
(S10a, decisiones del usuario en L16 de `odd/tasks/latencia.md`). Reemplaza la parte de
las palabras de riesgo de los resguardos de D20 y concreta el punto «confirmación según la
acción» de D22.

| Acción | ¿Pregunta? |
|---|---|
| Enter (`Return`, `ctrl+Return` o cualquier Enter en una secuencia, o un salto de línea en el texto) en una app de chat o correo, o en un navegador cuyo título muestra un chat o correo web (WhatsApp, Telegram, Gmail, Outlook, Discord, Messenger, Slack…) | Sí |
| Enter en un navegador en cualquier otra página (búsquedas, formularios) o en otra app | No |
| Texto o intención con palabras como «borrar», «enviar» o «pagar» | No; la intención solo se muestra en el aviso |
| Borrar todo el contenido: `ctrl+a` seguido de `Delete`, `BackSpace` o `ctrl+x`, o `g g d shift+g` de Vim | Sí |
| Cerrar una ventana o app: `alt+F4`, `ctrl+w`, `ctrl+q` | Sí |

Se mantienen sin cambios: la lista de bloqueo (terminales, contraseñas, ajustes), el
rechazo de letras que imitan palabras de riesgo (cirílicas o griegas), los 30 s de espera
y la denegación sin respuesta, sin TUI conectada o al revocar, y el aviso en la TUI. La
tabla vive en el código (`host/input/jarvis_input/checks.py`, `confirm_reasons`).

**Por qué:** con las palabras, el agente preguntaba por «entrar en modo inserción sin
borrar contenido» (desktop-e2e L5-L8) y no por un `ctrl+a Delete` con una intención
inocente; la confirmación dependía de cómo redactaba el modelo, no de lo que hacía.

**Descartado:** preguntar por Enter en cualquier navegador (pregunta en cada búsqueda) y
seguir preguntando por las palabras del texto escrito.

## Decisiones abiertas

- **Motor de la charla de voz** (D14).
- **Modelos concretos** de STT y TTS (D4) y modelo del agente.
- **Lista concreta de acciones** (D8).
- **Autenticación:** los tokens actuales son valores de desarrollo.
- **Resguardos del control de la PC** (D20): forma concreta del permiso, la parada de
  emergencia, la lista de bloqueo y las confirmaciones.
