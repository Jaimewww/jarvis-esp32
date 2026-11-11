# Planificación del Proyecto — 5 integrantes (3 hardware / 2 software)

Duración estimada: **8 semanas** (ajustable; cada fase tiene entregable y criterio de aceptación).
Los hitos están pensados para que siempre haya algo demostrable funcionando.

## Roles

### Equipo de hardware (3)

| Rol | Responsabilidades principales |
|---|---|
| **H1 — Hardware y energía** | Armado del protoboard, pinout, esquema de alimentación, filtrado de ruido, PCB/perfboard final |
| **H2 — Firmware edge (todo el ESP32-S3)** | Drivers I2S (INMP441 y MAX98357A), captura y streaming, WiFi/HTTPS, anillo WS2812B, botón PTT |
| **H3 — Mecánica 3D y QA hardware** | Diseño CAD de la caja, impresión e iteraciones, pruebas de audio/energía, documentación de hardware |

### Equipo de software (2)

| Rol | Responsabilidades principales |
|---|---|
| **S1 — Backend / IA** | Orquestador STT→LLM→TTS, System Prompt J.A.R.V.I.S., schema de acciones para la PC, WebSocket al host agent, API keys |
| **S2 — Host agent + TUI** | Agente en la PC que ejecuta las órdenes (teclas multimedia, apps, volumen), interfaz Textual con la conversación, integración con el backend |

> Notas de coordinación:
> - H2 es el dueño único del firmware: ninguna otra persona commitea en `firmware/` sin pasar por él.
> - S1 y S2 trabajan pegados: primero fijan el **contrato** (schema de acciones + WebSocket) en la
>   Fase 2, y luego cada uno avanza contra ese contrato sin bloquearse.
> - H3 diseña la caja en paralelo desde la semana 1 usando medidas reales que aporta H1.

## Cronograma

### Fase 0 — Setup y compras (Semana 1) — *todos*
- [ ] Comprar BOM (`materiales.md`): 1 kit + repuestos de mic y amp.
- [ ] Instalar Arduino IDE + ESP32 core 3.x; compilar y flashear el blink de fábrica (H2).
- [ ] Instalar Python 3.11 + entorno del host agent (S2); repo listo en GitHub.
- [ ] Crear cuenta Groq + OpenAI (o Gemini) y verificar API keys (S1).
- [ ] H3: empezar modelo de caja con medidas de los módulos.

**Entregable:** kit en mano, toolchain probada en ambos mundos (ESP32 y Python).

### Fase 1 — Banco de pruebas de hardware (Semanas 1–2) — H1, H2, H3
- [ ] Grabar audio del INMP441 y volcarlo por serial (verificar ruido y ganancia).
- [ ] Reproducir un WAV de prueba por el MAX98357A.
- [ ] Probar anillo WS2812B y botón.
- [ ] Medir el problema de alimentación: WiFi TX + audio simultáneos → poner condensadores.

**Criterio de aceptación:** se graba un clip de 5 s, se reproduce por el altavoz sin cortes,
y el color del anillo cambia por comando serial.

### Fase 2 — Pipeline de nube + contrato (Semanas 2–3) — S1, S2
- [ ] Script de prueba (Python) que: WAV → Groq STT → texto.
- [ ] Texto → LLM con System Prompt J.A.R.V.I.S. → respuesta + JSON de acción para la PC.
- [ ] Respuesta → TTS → MP3.
- [ ] **Congelar el contrato**: schema de acciones y formato del WebSocket backend ↔ host agent
      (documento `nube.md` actualizado; S1 y S2 lo firman juntos).

**Criterio de aceptación:** corriendo el script desde una laptop con un WAV grabado a mano,
se obtiene respuesta hablada correcta y el JSON de acción esperado.

### Fase 3 — Host agent + TUI v1 (Semanas 2–4, paralela) — S2
- [ ] Cliente WebSocket contra el backend: recibe acciones y estado del ciclo.
- [ ] Ejecución de acciones v1: teclas multimedia (play/pause, volumen), abrir apps.
- [ ] TUI Textual: transcripción de la conversación, log de acciones, indicador de estado.
- [ ] (Opcional) input de texto en la TUI: pregunta escrita → misma ruta del LLM.

**Criterio de aceptación:** desde la terminal, la TUI muestra la conversación y las órdenes
de voz se ejecutan realmente en la PC (se abre una app, cambia el volumen).

### Fase 4 — Integración extremo a extremo (Semanas 3–4) — H2, S1, S2
- [ ] ESP32 sube WAV por HTTPS y recibe audio de respuesta (PTT manual).
- [ ] Conversación completa funcionando: hablar → escuchar respuesta → acción en la PC.
- [ ] Medir latencia real por etapa y registrarla.

**Criterio de aceptación:** demo de ida y vuelta con < 3 s de espera percibida y la orden
ejecutándose en la PC antes de que termine la respuesta hablada.

### Fase 5 — Control completo de la PC (Semana 5) — S2, S1
- [ ] Catálogo ampliado: lanzar apps concretas, volumen por nivel, screenshot (v2).
- [ ] Manejo de errores: backend caído, acción desconocida, WiFi perdido (nunca colgarse).
- [ ] Anillo de estado sincronizado con el ciclo completo (azul/amarillo/verde/rojo).

**Criterio de aceptación:** 10 órdenes seguidas de PC (apps, volumen, media) con 100 % de ejecución.

### Fase 6 — Refinamiento (Semana 6) — H1, H2, S1
- [ ] Eliminar zumbido de alimentación definitivamente (o fuente separada).
- [ ] Probar VAD para reemplazar el PTT (opcional).
- [ ] Tuning de memoria: verificar cero reinicios en 30 min de uso continuo.

### Fase 7 — Caja impresa en 3D (Semanas 5–7, en paralelo) — H3, H1
- [ ] V1 impresa con las medidas del protoboard → ajuste de tolerancias.
- [ ] V2 con cámaras separadas (audio) y difusor del anillo LED.
- [ ] V3 final: perfboard montada, paso de cables, tapa con tornillos.

**Criterio de aceptación:** el equipo completo usa el prototipo montado en la caja V3.

### Fase 8 — Cierre (Semana 8) — *todos*
- [ ] Suite final de pruebas (`pruebas.md`).
- [ ] Documentación final: fotos, diagrama de conexiones, video demo.
- [ ] Preparar presentación: demo en vivo + plan B con video grabado.

## Hitos de control (checkpoints con el docente/equipo)

| Semana | Hito |
|---|---|
| 2 | Audio capturado y reproducido desde el ESP32 |
| 4 | Conversación voz-a-voz completa con acción ejecutada en la PC |
| 6 | Control de PC completo + prototipo en caja V2 |
| 8 | Demo final |

## Riesgos del cronograma

| Riesgo | Mitigación |
|---|---|
| Demora en envíos de componentes (AliExpress) | Comprar semana 0; buscar alternativas en marketplace local |
| Zumbido de audio no resuelto | Fase 1 ya lo mide; plan B: fuente separada 5 V para el amp |
| PSRAM insuficiente → panics | Comprar directamente N16R8; limitar tamaño de buffers |
| API keys con cuota agotada en demo | Cachear respuesta de error hablada; tener 2 proveedores configurados |
| Integración tarde (todo junto al final) | Contrato S1↔S2 congelado en Fase 2; integración forzada en semana 3–4 |
| Permisos del sistema para controlar la PC (SO restrictive) | Probar acciones desde Fase 3 (temprano); tener fallback con pyautogui/pynput |
