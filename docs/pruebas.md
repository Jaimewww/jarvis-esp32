# Plan de Pruebas y Matriz de Riesgos

Principio: cada fase de `planificacion.md` tiene su criterio de aceptación; esta lista es el
detalle de qué se ejecuta al final (Fase 8) y qué checks rápidos corren en cada fase.

Dueños: H1/H2/H3 = equipo de hardware, S1/S2 = equipo de software.

## A. Checks por subsistema

### Audio — captura (H2, H3)
- [ ] Grabar 5 s y volcar por serial: se escucha la voz clara, sin clipping (verificar con
      Audacity: picos < 0 dBFS).
- [ ] Silencio real ≈ silencio digital (línea base de ruido RMS medida y registrada).
- [ ] Grabación de 30 s sin cortes ni buffers perdidos.

### Audio — reproducción (H2)
- [ ] WAV de prueba a 16/24/44,1 kHz se reproduce con tono correcto (sin chipmunk).
- [ ] Reproducción mientras WiFi transmite: sin clics (el clásico caso del buzz).

### Red (H2)
- [ ] POST del WAV al orquestador con audio válido de 5 s: HTTP 200.
- [ ] Reconexión automática al WiFi tras apagar el router 30 s.
- [ ] Timeout de red (5 s) → estado ERROR → vuelve a IDLE sin reiniciar la placa.

### Nube (S1)
- [ ] 10 audios de prueba con acento rioplatense → transcripción correcta ≥ 9/10.
- [ ] Orden de PC: "poné música" → JSON `{"type":"media_key","value":"play_pause"}`.
- [ ] Orden de PC: "abrí Spotify" → JSON `{"type":"app","value":"open:spotify"}`.
- [ ] Pregunta general ("¿qué hora es?") → respuesta hablada, `actions: []`.
- [ ] Sin API key / cuota agotada → mensaje de error hablado, no crash del orquestador.

### Host agent + TUI (S2)
- [ ] Acción `media_key` ejecutada: el reproductor de la PC responde.
- [ ] Acción `app` ejecutada: la app se abre (y se muestra ✓ en la TUI).
- [ ] Acción `volume` ejecutada: el volumen del SO cambia al valor pedido.
- [ ] Acción desconocida → ✗ en la TUI + reporte al backend, sin crash del agente.
- [ ] Backend caído → la TUI marca la desconexión y reconecta sola al volver.
- [ ] Acción llega **antes** de que termine la respuesta hablada (orden no espera al audio).

### Integración (todos)
- [ ] 20 conversaciones seguidas: ≥ 18 exitosas, cero reinicios de la placa.
- [ ] Latencia media registrada por etapa (tabla en arquitectura.md como referencia).
- [ ] Orden de PC ejecutada en < 500 ms desde que el backend la emite.
- [ ] Estado de la TUI sincronizado con el anillo LED (mismo ciclo).

### Alimentación (H1)
- [ ] WiFi TX pico + reproducción simultánea: sin brownout, sin buzz audible
      (probar con y sin condensadores para documentar la diferencia).
- [ ] 30 min de uso continuo sin `Guru Meditation` ni reinicios.

## B. Prueba final de aceptación (demo)

1. Prender el dispositivo → anillo azul (reposo); TUI marca ESP32 online.
2. PTT: "J.A.R.V.I.S., ¿cómo estás?" → respuesta hablada sarcástica en el altavoz,
   transcript visible en la TUI. *(anillo amarillo→verde)*
3. PTT: "Poné música" → play/pause ejecutado en la PC + respuesta hablada + ✓ en TUI.
4. PTT: "Abrí la calculadora" → app abierta en la PC.
5. PTT: "Bajá el volumen" → volumen del SO cambia.
6. Desconectar WiFi → comando → anillo rojo + error hablado + TUI muestra desconexión →
   reconexión automática.
7. Todo el prototipo montado en la caja V3.

## C. Matriz de riesgos técnicos (consolidada)

| Riesgo | Prob. | Impacto | Mitigación | Dueño |
|---|---|---|---|---|
| Zumbido de alimentación con WiFi TX | Alta | Medio | 1000 µF + 100 nF; fuente separada para el amp | H1 |
| Reinicios por falta de RAM | Media | Alto | PSRAM obligatoria; buffers a `ps_malloc()` | H2 |
| Latencia total > 4 s | Media | Medio | Streaming TTS, modelo LLM chico, keep-alive | S1 |
| Permisos del SO bloquean control de la PC | Media | Alto | Probar acciones desde Fase 3; fallback pyautogui/pynput; fijar un SO de demo | S2 |
| Falsos positivos con VAD | Media | Bajo | PTT como modo primario; VAD solo en v2 | H2 |
| Acento no reconocido por Whisper | Baja | Medio | `language=es`, probar large-v3 vs distil | S1 |
| Impresión con tolerancias flojas | Alta | Bajo | Pieza de prueba de tolerancias antes del modelo final | H3 |
| Cuota de API agotada en la demo | Baja | Alto | Doble proveedor configurado; cache de errores | S1 |
| Componente quemado (mic/amp) | Media | Medio | Comprar repuestos desde el día 1 | H1 |

## D. Registro de bugs

Llevar en `docs/bugs.md` (se crea cuando aparezca el primero) con formato:
`ID | Fase | Síntoma | Causa raíz | Fix | Cómo se verificó`. Los bugs de integración
(aquello de "en mi protoboard funciona") son oro para la documentación final.
