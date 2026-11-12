# J.A.R.V.I.S. de Escritorio — ESP32-S3 + computadora + TUI

Diseño actualizado con el informe del **11 de noviembre de 2026**: el ESP32-S3 detecta
«Jarvis», captura y reproduce audio; la computadora coordina transcripción, agente,
síntesis de voz y acciones sobre la propia PC. El anillo WS2812 muestra el estado.

## Estado actual

El repositorio contiene documentación de planificación. Todavía no incluye firmware,
agente anfitrión ni modelos CAD. Las capacidades siguientes son objetivos, no funciones
implementadas o verificadas en hardware. No hay mediciones de latencia del sistema.

## Estructura actual del repositorio

| Documento | Contenido |
|---|---|
| [Arquitectura](docs/arquitectura.md) | Responsabilidades, flujo y alcance |
| [Materiales](docs/materiales.md) | BOM consultada el 2026-11-11 y conexiones propuestas |
| [Planificación](docs/planificacion.md) | Fases y cinco roles funcionales |
| [Firmware](docs/firmware.md) | Activación local, audio, red y decisiones pendientes |
| [Backend e IA](docs/nube.md) | Comparación STT, Gentle Shell y alternativas |
| [Host y TUI](docs/tui.md) | Conversación, estado y registro de acciones |
| [Caja 3D](docs/caja-3d.md) | Diseño mecánico previsto |
| [Pruebas](docs/pruebas.md) | Validación futura y riesgos |

Las carpetas `firmware/` (Fase 1), `host/` (Fase 2) y `cad/` (Fase 7) están
previstas, pero aún no existen.

## Decisiones de diseño — 2026-11-11

| Tema | Diseño vigente |
|---|---|
| Activación | «Jarvis» en el ESP32-S3 con WakeNet/ESP-SR; pulsador solo como respaldo. Compatibilidad e integración por validar. |
| Captura | Orden a 16 kHz, detección de fin del habla y envío por Wi-Fi a la computadora. |
| Transcripción | Comparar Whisper con Parakeet local en la primera fase; sin selección final. |
| Agente | Gentle Shell en segundo plano, sin interfaz propia; Gentle Dot es referencia, no integración verificada. |
| Voz y acciones | La PC coordina STT, razonamiento y TTS, ejecuta acciones y devuelve audio al ESP32. Esto no garantiza un stack totalmente offline. |
| Estado | Reposo, escuchando, pensando, hablando y error en anillo y TUI. |
| Hardware | ESP32-S3 N16R8, INMP441, MAX98357A, parlante de 45 mm y anillo de 8 LED. |
| Presupuesto | USD 50,64 obligatorios + 13,94 opcionales = 64,58; sin envío ni filamento, con salvedades de IVA. |

Esta actualización sustituye el diseño anterior de nube como centro obligatorio, PTT
primario y proveedores fijos. Las opciones anteriores solo siguen como candidatas donde
se indican. No se ha cerrado un protocolo RPC ni el contrato entre componentes.

## Flujo y límites

Voz → activación local → captura → Wi-Fi → PC (STT → Gentle Shell → TTS)
→ audio al ESP32 → altavoz. Las acciones se ejecutan en la PC y se registran en la TUI.

Fuera de alcance: domótica u otros periféricos, funcionamiento sin computadora encendida
y conectada a la misma red, batería, identificación del hablante y uso multiusuario simultáneo.
La alimentación es por USB.

## Antes de implementar

- Validar WakeNet y el modelo de «Jarvis» en la placa, con voces del equipo y ruido de aula.
- Comparar STT, elegir TTS y definir cómo integrar Gentle Shell y la TUI.
- Acordar transporte, autenticación, formatos de audio y acciones; no hay contrato aprobado.
- Medir latencia y consumo en lugar de asumir cifras del diseño anterior.

## Cómo trabajamos

- Cada fase del [plan](docs/planificacion.md) tiene criterios de aceptación previstos.
- Los cambios de alcance se documentan; esta documentación describe el diseño vigente.
- Commits con Conventional Commits.
