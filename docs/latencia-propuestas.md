# Latencia: propuestas de arquitectura

Estado: **propuesta para discutir**, no es una decisión. Cuando el equipo acuerde, lo
elegido pasa a `docs/decisiones.md` (revisa D13, D14 y D17).

## Resumen

- El turno es lento porque **el agente consulta al modelo remoto en cada paso**, no por
  la red local ni por el daemon. Una consulta tarda entre 2,6 y 5,6 s; un pedido como
  «abrí VS Code y escribí una frase» hizo **7 consultas** y tardó cerca de 45 s.
- Hoy todo pasa por el agente: el camino rápido de D13 está diseñado pero no existe.
- Los proyectos parecidos resuelven lo mismo con **capas**: primero un reconocedor sin
  IA para las órdenes conocidas, después un modelo, y la respuesta hablada en
  **tubería** (cada etapa empieza antes de que termine la anterior).
- Propuesta: cinco cambios ordenados por impacto y costo (P1 a P5). Con P1 y P2 las
  órdenes del catálogo bajarían de ~4,6 s a menos de 1,5 s, y las tareas de control de
  la PC de ~45 s a ~10-15 s. **Todos los tiempos previstos son estimaciones a medir**;
  la sección «Cómo medimos» dice cómo.
- La IA local queda para una etapa de medición (P5), no se adopta todavía.

## Cómo es un turno hoy

```
TUI (texto)  ─────────────────────────────┐
F4 (voz) ─► whisper-server :8080 (0,2-0,3 s) ─┤
                                              ▼
                              daemon Gentle Dot :4317 (0,03 s)
                                              │  bucle del agente
                                              ▼
                              modelo remoto openai/gpt-6.1-sol, pensamiento bajo
                              (2,6-5,6 s por consulta)
                                              │  cada herramienta = otra consulta
                                              ▼
                              herramientas pc_* → jarvis-input → wtype / grim / niri
```

No hay síntesis de voz (TTS) implementada todavía, ni camino rápido.

### Medido

| Tramo | Tiempo | Fuente |
|---|---|---|
| TUI → daemon → TUI, sin modelo (eco) | 0,03 s | `odd/tasks/s2-round2.md` L8 |
| STT whisper.cpp en la GPU (3-6 s de audio) | 0,18-0,33 s | s2-round2 L9, L12 |
| Respuesta trivial sin herramientas, sol con pensamiento medio | 5,07 s | s2-round2 L8 |
| Igual, sol con pensamiento bajo (el actual) | 3,36-3,97 s | s2-round2 L8 |
| Igual, terra bajo / luna sin pensamiento | 2,88 s / 2,57 s | s2-round2 L8 |
| «abre spotify» (una herramienta) | 4,6 s | s2-round2 L10 |
| «abrí VS Code y escribí una frase larga» (21-11, 13:03-13:04) | ~48 s | registro de `jarvis-input` |

Desglose del último pedido, a partir del registro de `jarvis-input`:

| Paso | Hora | Hueco |
|---|---|---|
| listar ventanas | 13:03:45,8 | — |
| abrir VS Code | 13:03:50,3 | 4,5 s |
| listar ventanas | 13:03:55,9 | 5,6 s |
| captura | 13:04:00,1 | 4,2 s |
| tecla `i` | 13:04:05,4 | 5,3 s |
| confirmación del usuario | 13:04:11,5 | 6,1 s (humano) |
| escribir 305 caracteres | 13:04:19,5 | 8,0 s (tecla + consulta) |
| confirmación del usuario | 13:04:23,2 | 3,6 s (humano) |
| captura final | 13:04:33,4 | 10,2 s (escritura ~5 s + consulta) |

Cuenta: unas **7 consultas al modelo de 4-5,5 s (~32 s)**, ~10 s de confirmaciones y
~5 s de escritura. La red local y el daemon no pesan.

### Dónde se va el tiempo, por orden

1. **Una consulta al modelo por cada paso.** El flujo que pide `host/gentle-dot/AGENTS.md`
   (listar, abrir, volver a listar, verificar con captura) multiplica las consultas.
2. **Todo pasa por el agente.** Incluso una orden fija como abrir una aplicación.
3. **Confirmaciones disparadas por palabras.** «borrar» en la intención pidió
   confirmación aunque la intención decía «sin borrar».
4. **Capturas enviadas al modelo** (más tokens de entrada por consulta; no medido).

## Qué hacen proyectos parecidos

| Proyecto | Arquitectura | Qué tomamos |
|---|---|---|
| **Home Assistant Assist** (Voice PE) | Un agente de conversación local **sin IA** reconoce frases fijas («encendé la luz de la cocina»); solo lo que no reconoce va a un modelo (OpenAI, Ollama). Opción «preferir comandos locales». | Es exactamente D13: reconocedor de frases primero, modelo después. Ojo: un hilo de la comunidad reporta que con esa opción mal configurada una pregunta pasó de ~2,5 s a ~52 s; el reconocedor tiene que fallar rápido y no reintentar. |
| **Benchmark de modelos locales para Assist** (exitcode0.net, RTX 3060 12 GB) | Ollama como agente con herramientas, 9 órdenes × 2. | qwen3.5:9b: 18/18 aciertos, 2,0 s de mediana. gemma4:e2b: **0,7 s pero solo 6/18 aciertos**. Un modelo chico es rápido pero se equivoca en las herramientas; uno de 9B no entra en los 6 GB de la 4050 junto con Whisper. |
| **XiaoZhi** (ESP32, el más popular en GitHub) | El ESP32 solo hace activación, audio (Opus) y ejecutar herramientas; ASR, LLM y TTS corren en un servidor por WebSocket, con flujo continuo. | Igual que nuestro reparto D2/D5. Confirma que el ESP32 no es donde se gana tiempo; se gana en el servidor. |
| **Willow** (ESP32-S3-BOX) | Reconocimiento de órdenes **en el propio ESP32** (ESP-SR MultiNet, hasta 200-300 órdenes) o un servidor de inferencia propio optimizado para CUDA (Whisper medium: 3,8 s de habla en 588 ms en una GTX 1060). | Las órdenes más comunes podrían reconocerse en el ESP32 sin pasar por el STT de la PC. MultiNet admite inglés y chino, **no español**: descartado para nosotros salvo frases en inglés. |
| **Open Interpreter 01 Light** | ESP32 de pulsar para hablar; la voz va por Wi-Fi a un servidor que corre STT → Open Interpreter (agente que controla la PC) → TTS. | El mismo problema que nuestra tarea larga: un agente que controla la PC es lento por naturaleza; se lo trata como tarea, no como charla. |
| **Pipecat** (marco de agentes de voz) | Tubería: STT en flujo, el LLM empieza al detectar el fin del habla (~200 ms de silencio), el TTS sintetiza frase por frase mientras llegan los tokens. Apuntan a ~800 ms de voz a voz; logran ~500-700 ms con modelos locales. | La respuesta por frases de D17 (punto 3). Una persona espera ~500 ms en una conversación; más de unos segundos sin señal se siente roto. |

Fuentes: home-assistant.io/voice_control; community.home-assistant.io (hilo
«Why "Prefer handling commands locally" increases the time from ~2.5s to ~52s?»);
exitcode0.net/posts/home-assistant-ollama-conversation-agent-local-llm (2026-11-03);
github.com/78/xiaozhi-esp32 y xiaozhi.dev; heywillow.io y github.com/HeyWillow/willow
discussion #55; docs.espressif.com/projects/esp-sr (MultiNet); 01.openinterpreter.com;
docs.pipecat.ai y github.com/pipecat-ai/nemotron-january-2026.

**Patrón común:** nadie usa el modelo grande para todo. Las órdenes conocidas no tocan
un modelo, la charla usa el modelo más rápido que alcance, y lo que exige razonar o
mirar la pantalla se trata como tarea con avisos de progreso.

## Propuestas

Ordenadas por impacto sobre costo. Cada una dice qué cambia, el tiempo previsto y el
riesgo. Los tiempos previstos salen de las mediciones de arriba; hay que confirmarlos.

### P1 — Camino rápido real (D13)

Un reconocedor de frases sin IA, antes del agente: «abrí <app>», «subí/bajá el
volumen», «pausá/siguiente», «escribí <texto> en <app>». Si reconoce, ejecuta directo
por `jarvis-input` o el catálogo; si no, pasa al agente sin demora (falla rápido).

| | Hoy | Previsto |
|---|---|---|
| «abrí Spotify» por voz | ~4,9 s (STT + agente) | **~0,5-1 s** (STT 0,3 s + reconocer ~0 + ejecutar) |

Riesgo: bajo. Es la medida de mayor impacto y ya estaba decidida. Hay que acordar
dónde vive (backend, de Jaime, o la TUI, mía) y la lista de frases.

### P2 — Menos consultas por tarea

- **Herramientas compuestas**: `pc_write_in(app, texto)` abre o enfoca la aplicación,
  espera y escribe en una sola llamada; `pc_run(app, teclas)` igual para atajos.
- **Sin verificación por defecto**: el agente no saca captura después de cada acción;
  solo si el usuario pide verla o si algo falló.
- **Confirmación por la acción, no por palabras de la intención**: evita casos como
  «sin borrar» (revisar con cuidado: es una barrera de seguridad).
- Ajustar `AGENTS.md` para no listar ventanas dos veces.

| | Hoy | Previsto |
|---|---|---|
| «abrí VS Code y escribí una frase» | ~48 s, 7 consultas | **~10-15 s**, 2 consultas (decidir + resumen) |

Riesgo: medio. Cambia el contrato de herramientas del agente (extensión y `jarvis-input`)
y la política de confirmación; necesita pruebas de escritorio y revisión.

### P3 — Modelo y pensamiento por tipo de pedido

El daemon permite elegir modelo y nivel de pensamiento. Usar el más rápido que alcance
para la charla (luna sin pensamiento: 2,57 s frente a 3,36-3,97 s del actual) y dejar
sol para la tarea larga.

| | Hoy | Previsto |
|---|---|---|
| Pregunta corta | ~3,4-4 s | **~2,6 s** |

Riesgo: bajo, pero el daemon tiene un solo agente y una sola conversación (D14): cambiar
de modelo entre turnos afecta a todos los clientes. Hay que medir si cambiarlo en cada
turno cuesta tiempo.

### P4 — Respuesta percibida y por frases (D17, puntos 3 y 4)

- Sonido corto y luz al terminar de hablar (el usuario sabe que se escuchó).
- TTS frase por frase: empieza a sonar la primera frase mientras el modelo sigue.
- Para tareas largas: «Dale, lo hago» inmediato y aviso al terminar (D14).

| | Hoy | Previsto |
|---|---|---|
| Tiempo hasta la primera señal | sin TTS; la TUI muestra «thinking» | **< 0,5 s** (sonido), primera frase hablada ~1 s después del primer texto del modelo |

Riesgo: bajo. Depende de elegir el TTS (D4: Piper es el primer candidato).

### P5 — Medir IA local (sin adoptarla todavía)

Instalar un motor local (llama.cpp u Ollama) y medir 2-3 modelos de 3-4B cuantizados
que entren en ~4-5 GB junto con Whisper, en dos papeles: **reconocer intención con
parámetros** (el respaldo de P1 cuando la frase no es exacta) y **charla corta**.

Máquina: RTX 4050 Laptop de 6 GB (0,9 GB en uso en reposo), 14 GB de RAM, 12 núcleos;
sin motor local instalado.

| | Remoto hoy | Local, a medir |
|---|---|---|
| Respuesta corta | 2,6-4 s | probable ~0,5-1,5 s (referencia: 0,7 s un modelo chico en una 3060) |
| Acierto en herramientas | alto | **dudoso**: el modelo chico del benchmark acertó 6/18 |

Riesgo: medio. Compite por VRAM con Whisper; un modelo chico no sirve para mirar la
pantalla y controlar la PC. Por eso se mide antes de decidir.

### Descartado por ahora

- **Reconocer órdenes en el ESP32 (MultiNet):** no hay modelo en español.
- **Todo local:** los modelos que entran en 6 GB no alcanzan para el control de la PC.
- **Audio en flujo continuo del ESP32:** D17 lo deja fuera hasta que una medición lo
  justifique; el STT ya tarda solo 0,2-0,3 s.

## Comparación

| Pedido | Hoy | Con P1+P2 | Con P1-P4 | Meta D17 |
|---|---|---|---|---|
| Orden del catálogo («abrí Spotify») | ~4,9 s | ~0,5-1 s | ~0,5-1 s | 1,5 s |
| Pregunta corta | ~3,7-4,3 s | igual | ~2,9 s; primera señal < 0,5 s | 4 s |
| Tarea de control de la PC | ~48 s | ~10-15 s | ~10-15 s, con aviso inmediato | sin meta (se mide la confirmación) |

Valores «hoy» = STT 0,3 s + lo medido. Las demás columnas son estimaciones.

## Cómo medimos

1. **Una medición por etapa en cada turno** (D17 ya lo pide): fin del habla, STT,
   reconocimiento, cada consulta al modelo, cada herramienta, primera señal, fin. Hoy
   la TUI cronometra solo hasta el primer `message_done` (s2-round2 L10); hay que medir
   el turno entero.
2. **Un juego fijo de pedidos** (10-15: órdenes del catálogo, preguntas cortas, 3 tareas
   de control de la PC), corrido con `python -m jarvis.bench` antes y después de cada
   propuesta, con mediana y peor caso.
3. **Para P5**, el mismo juego con cada modelo local, contando aciertos por el
   resultado en la PC y no por lo que dice el modelo (como el benchmark de exitcode0).

## Para decidir con el equipo

1. ¿Dónde vive el camino rápido: backend (S1) o TUI y host (S2)? Afecta el contrato.
2. ¿Qué frases entran al catálogo inicial?
3. ¿Aceptamos herramientas compuestas y quitar la verificación por captura por defecto?
4. ¿Cómo debe confirmar el control de la PC: por tipo de acción en vez de por palabras?
5. ¿Qué TTS usamos para la respuesta por frases?
6. ¿Hacemos el benchmark de IA local (P5) y con qué modelos?
