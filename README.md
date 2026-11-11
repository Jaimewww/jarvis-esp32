# J.A.R.V.I.S. de Escritorio — Asistente de voz con ESP32-S3 + TUI

Asistente de voz híbrido (Edge + Cloud): un ESP32-S3 captura audio, lo envía a la nube para
transcribir (STT), razonar (LLM) y sintetizar voz (TTS), reproduce la respuesta por altavoz y
**controla la computadora** mediante un agente anfitrión con interfaz TUI. El anillo WS2812B
refleja el estado del asistente (núcleo estilo J.A.R.V.I.S.).

## Estructura del repositorio

```
jarvis-esp32/
├── README.md              ← estás acá
├── docs/
│   ├── arquitectura.md    Cómo funciona el sistema, flujo, presupuesto de latencia
│   ├── materiales.md      Lista de materiales (BOM) con costos estimados y conexiones
│   ├── planificacion.md   Plan maestro: fases, cronograma semanal, roles (3 HW / 2 SW)
│   ├── firmware.md        Plan de firmware: capas, librerías, manejo de memoria I2S + WiFi
│   ├── nube.md            Stack de IA: Groq/Whisper, LLM con prompt J.A.R.V.I.S., TTS, protocolo
│   ├── tui.md             Host agent en la PC + TUI (Textual): conversación y control del sistema
│   ├── caja-3d.md         Especificaciones de la carcasa impresa en 3D
│   └── pruebas.md         Plan de pruebas por fase + matriz de riesgos
├── firmware/              (Fase 1) código del ESP32-S3
├── host/                  (Fase 2) agente anfitrión + TUI en Python
└── cad/                   (Fase 6) modelos 3D de la carcasa
```

## Resumen de una línea de flujo

Micrófono I2S → ESP32-S3 → WiFi → API STT → LLM (con intención JSON) → TTS → audio I2S → altavoz
→ (si hay orden) host agent en la PC ejecuta el comando (teclas, apps, volumen) y la TUI lo muestra.

## Cómo trabajamos

- Cada fase de `docs/planificacion.md` tiene criterios de aceptación verificables.
- Los cambios de alcance se documentan; este directorio es la única fuente de verdad.
- Commits con Conventional Commits.
