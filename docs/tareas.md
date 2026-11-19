# Tareas por pista

Qué hace cada pista y en qué orden, según el reparto de la
[decisión D21](decisiones.md#d21--reparto-del-trabajo). Las fronteras entre pistas son
los [contratos](contratos.md). El plan por fases y semanas sigue en
[planificación](planificacion.md).

**Antes de empezar:** fusionar la solicitud que introduce los contratos. Hasta entonces
todavía se pueden discutir.

## Quién toca qué

| Pista | Rol | Carpetas propias |
|---|---|---|
| Voz | S1 | `backend/`, `tools/` |
| Agente y pantalla | S2 | `host/` y el archivo `backend/app/agents/gentle_dot.py` |
| Dispositivo | Por asignar cuando llegue la placa | `firmware/` |

Compartidos: `README.md`, `docs/decisiones.md` y `docs/contratos.md`. Se cambian en una
solicitud propia, sin mezclar con código, y la aprueba la otra pista.

El archivo `.github/CODEOWNERS` hace que GitHub pida revisión al responsable cuando una
solicitud toca una carpeta ajena.

## Pista de voz (S1)

| # | Tarea | Listo cuando |
|---|---|---|
| V1 | STT real en `/converse`, detrás de la interfaz de transcripción | Un WAV grabado en español devuelve su texto, con el tiempo de la etapa |
| V2 | TTS real, detrás de la interfaz de síntesis | La respuesta es audio WAV de 16 kHz, mono, 16 bits |
| V3 | Camino rápido y catálogo de acciones | Una orden del catálogo se ejecuta sin pasar por el agente |
| V4 | Motor de la charla de voz, elegido con mediciones | Una pregunta corta se responde dentro de la meta de 4 s |
| V5 | Evento `turn_error`, eventos `turn_stage` y respuesta `409` | Un turno que falla siempre termina con un evento de cierre |

## Pista de agente y pantalla (S2)

| # | Tarea | Listo cuando |
|---|---|---|
| P1 | Conectar la TUI al WebSocket del backend | La TUI muestra el estado del ESP32 y los turnos usando el simulador |
| P2 | Mejorar la presentación de la TUI | Estado, conversación y acciones se leen de un vistazo |
| P3 | Daemon de Gentle Dot como servicio | Arranca solo, sin la aplicación de escritorio, y la TUI se conecta |
| P4 | Adaptador de Gentle Dot para tareas largas | Cumple la interfaz de agente del contrato C3 y pasa sus pruebas con un daemon real |

## Dónde se tocan las pistas

| Punto de contacto | Quién lo define | Quién lo usa | Contrato |
|---|---|---|---|
| Eventos del WebSocket | Voz | Agente y pantalla (P1) | C2 |
| Interfaz de agente | Voz | Agente y pantalla (P4) | C3 |
| Forma de una acción | Voz | Agente y pantalla (la TUI la muestra) | C4 |

Ninguna tarea espera a la otra pista: P1 y P2 se prueban con `tools/sim_device.py`, y
V1 a V4 con las etapas falsas de `backend/app/fakes.py`.

## Antes de empezar una tarea

1. **¿Todos los archivos que voy a tocar están en mis carpetas?** Si no, hablar primero.
2. **¿Cambio un campo, un evento o un endpoint de los contratos?** Si sí, primero una
   solicitud que cambie solo `docs/contratos.md`, aprobada por la otra pista.
3. **¿Mi rama sale del `main` actual?** Actualizarla antes de abrir la solicitud.
