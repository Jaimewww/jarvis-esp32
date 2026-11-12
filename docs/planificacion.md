# Planificación — cinco roles (3 hardware / 2 software)

Actualización del **2026-11-11**. Todas las fases están pendientes. Se conserva una
estimación de ocho semanas, ajustable; no es evidencia de avances ni calendario confirmado.

## Roles funcionales

| Rol | Responsabilidad |
|---|---|
| H1 — Montaje y alimentación | Protoboard, alimentación, filtrado y soldadura final. |
| H2 — Luces, audio y activación | Esquema y pines, WS2812, controladores I2S y palabra «Jarvis». |
| H3 — Carcasa e impresión | Modelado, impresión y ajuste de placa, altavoz y anillo. |
| S1 — Backend | Envío de audio por Wi-Fi desde firmware, transcripción, agente y síntesis. |
| S2 — TUI, acciones y pruebas | Interfaz de terminal, acciones en la PC y plan de pruebas. |

H2 y S1 coordinan audio/red del firmware; S1 y S2 acuerdan la integración del agente.
H3 usa medidas reales aportadas por montaje. No se impone un dueño único de todo el firmware.

## Fases y aceptación prevista

| Fase / semana estimada | Trabajo | Evidencia para aceptar |
|---|---|---|
| 0 / 1 | Revisar BOM y compras, elegir entorno compatible con ESP-SR. | Kit y herramientas comprobados, costos y envíos confirmados. |
| 1 / 1–2 | Banco de audio, LEDs, alimentación y WakeNet; iniciar comparación Whisper/Parakeet local. | Clip grabado/reproducido, estados visibles, resultados iniciales de activación y STT. |
| 2 / 2–3 | Pipeline en PC, Gentle Shell en segundo plano y acuerdos de comunicación. | Audio de prueba produce texto y voz; integración documentada sin asumir RPC. |
| 3 / 2–4 | TUI y acciones en PC, en paralelo. | Conversación visible, app abierta y volumen cambiado con registro de resultado. |
| 4 / 3–4 | Integración por Wi-Fi con activación «Jarvis» y fin del habla. | Turno completo sin pulsar; tiempos por etapa medidos. |
| 5 / 5 | Robustez de acciones, estados y errores. | Órdenes repetidas y fallos de red/agente con resultados registrados. |
| 6 / 6 | Afinar activación, audio y memoria. | Aciertos/falsas activaciones medidos en aula; 30 min sin reinicios como objetivo. |
| 7 / 5–7 | Caja y montaje en paralelo. | Ajuste físico, acústica, acceso USB y pulsador de respaldo comprobados. |
| 8 / 8 | Pruebas finales y documentación. | Demo, fotos, conexiones y resultados; video de respaldo. |

No comprar cuentas de proveedores ni congelar contratos del diseño anterior como requisito.
Elegir STT después de comparar; validar TTS y el mecanismo de integración de Gentle Shell.

## Riesgos de planificación

- Precios, IVA y envíos requieren confirmación antes de comprar; filamento no presupuestado.
- WakeNet necesita pruebas tempranas de voces y ruido; conservar pulsador de respaldo.
- Integración del agente y permisos del SO pueden retrasar acciones: probarlos temprano.
- Tareas largas requieren estados visibles, no una promesa de respuesta en menos de 3 s.

Consultar [materiales](materiales.md), [pruebas](pruebas.md) y [caja](caja-3d.md).
