# J.A.R.V.I.S. de Escritorio — ESP32-S3 + computadora + TUI

Diseño actualizado con el informe del **11 de noviembre de 2026**: el ESP32-S3 detecta
«Jarvis», captura y reproduce audio; la computadora coordina transcripción, agente,
síntesis de voz y acciones sobre la propia PC. El anillo WS2812 muestra el estado.

## Estado actual

Hay dos piezas de software iniciadas y ninguna de hardware:

- `backend/`: contrato de transporte v0 (latido, conversación y WebSocket). La
  transcripción, el agente y la síntesis todavía devuelven respuestas de prueba.
- `host/`: TUI cliente del daemon de Gentle Dot. Aún no se conecta al backend.

No hay firmware ni modelos CAD. Las capacidades siguientes son objetivos, no funciones
verificadas en hardware. No hay mediciones de latencia del sistema.

## Estructura actual del repositorio

| Documento | Contenido |
|---|---|
| [Decisiones](docs/decisiones.md) | Decisiones de arquitectura del 2026-11-18 y su porqué |
| [Contratos](docs/contratos.md) | Fronteras entre firmware, backend y host, y dobles para probar |
| [Arquitectura](docs/arquitectura.md) | Responsabilidades, flujo y alcance |
| [Materiales](docs/materiales.md) | BOM consultada el 2026-11-11 y conexiones propuestas |
| [Planificación](docs/planificacion.md) | Fases y cinco roles funcionales |
| [Firmware](docs/firmware.md) | Activación local, audio, red y decisiones pendientes |
| [Backend e IA](docs/nube.md) | Comparación STT, Gentle Shell y alternativas |
| [Host y TUI](docs/tui.md) | Conversación, estado y registro de acciones |
| [Caja 3D](docs/caja-3d.md) | Diseño mecánico previsto |
| [Pruebas](docs/pruebas.md) | Validación futura y riesgos |

El código está en [`backend/`](backend/README.md) y [`host/`](host/README.md). Las
carpetas `firmware/` (Fase 1) y `cad/` (Fase 7) están previstas, pero aún no existen.

## Decisiones de diseño

El diseño es del informe del 2026-11-11; las decisiones de arquitectura del 2026-11-18
están en [decisiones](docs/decisiones.md) y prevalecen donde difieran.

| Tema | Diseño vigente |
|---|---|
| Activación | «Jarvis» en el ESP32-S3 con WakeNet/ESP-SR; pulsador solo como respaldo. Compatibilidad e integración por validar. |
| Firmware | ESP-IDF. |
| Captura | Orden a 16 kHz, detección de fin del habla en el ESP32 y envío por Wi-Fi con el contrato HTTP v0. |
| Transcripción y voz | STT y TTS locales en la PC. Modelo por elegir: Whisper frente a Parakeet para STT, Piper como primer candidato de TTS. |
| Agente | Daemon de Gentle Dot como dependencia externa, detrás de un adaptador del backend. Integración sin verificar. |
| Acciones | Catálogo de acciones de bajo riesgo sin confirmación y por camino rápido; lo demás va al agente. |
| Estado | Reposo, escuchando, pensando, hablando y error en anillo y TUI. |
| Hardware | ESP32-S3 N16R8, INMP441, MAX98357A, parlante de 45 mm y anillo de 8 LED. |
| Presupuesto | USD 50,64 obligatorios + 13,94 opcionales = 64,58; sin envío ni filamento, con salvedades de IVA. |

El diseño anterior de nube como centro obligatorio, PTT primario y proveedores fijos
quedó sustituido.

## Flujo y límites

Voz → activación local → captura → Wi-Fi → backend en la PC (STT → agente → TTS)
→ audio al ESP32 → altavoz. Las acciones se ejecutan en la PC y se registran en la TUI.

Fuera de alcance: domótica u otros periféricos, funcionamiento sin computadora encendida
y conectada a la misma red, batería, identificación del hablante y uso multiusuario simultáneo.
La alimentación es por USB.

## Qué sigue

- Primer hito: un turno completo desde la PC, con STT, agente y TTS reales y tiempos
  por etapa, sin esperar al hardware.
- Validar WakeNet y el modelo de «Jarvis» en la placa, con voces del equipo y ruido de aula.
- Cerrar las [decisiones abiertas](docs/decisiones.md#decisiones-abiertas): PC de la
  demostración, modelos, catálogo de acciones, autenticación y pruebas.

## Cómo trabajamos

- Cada fase del [plan](docs/planificacion.md) tiene criterios de aceptación previstos.
- Los cambios de alcance se documentan; esta documentación describe el diseño vigente.
- Commits con Conventional Commits.
