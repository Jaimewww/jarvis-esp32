# Host Agent + TUI de J.A.R.V.I.S. (PC)

El host agent es un daemon de Python que corre en la computadora del usuario. Hace dos cosas:
**ejecuta las órdenes** que el backend le manda por WebSocket y **muestra la interfaz** del
asistente en la terminal (TUI).

Stack: **Python 3.11+ + Textual** (TUI) + `websockets` + `pynput`/`pyautogui` (control del
sistema). Estructura propuesta en `host/`.

## Responsabilidades del host agent

1. **Ejecutar acciones.** Consume el JSON de acciones del backend y las aplica al sistema:
   teclas multimedia, abrir/cerrar apps, volumen, screenshots.
2. **Estado en vivo.** Se suscribe al ciclo del asistente (IDLE → THINKING → SPEAKING) para
   reflejarlo en la TUI — sincronizado con el anillo LED del ESP32.
3. **Transcripción.** Muestra la conversación (lo que dijo el usuario y lo que respondió
   J.A.R.V.I.S.).
4. **Input escrito (opcional v2).** Permitir tipear una pregunta en la TUI y enviarla por la
   misma ruta del LLM; la respuesta se muestra en la TUI y se habla desde el ESP32.

## TUI — Layout (Textual)

```
┌──────────────────────────────────────────────────────────────────────┐
│  J.A.R.V.I.S. ● online          backend ✓   esp32 ✓        12:04     │ header
├──────────────────────────────────────────┬───────────────────────────┤
│  Conversación                            │  Acciones                 │
│                                          │                           │
│  ▸ usuario: "poné música de focus"       │  12:03 ✓ app spotify      │
│  ▸ jarvis:  "Con gusto, señor."          │  12:03 ✓ media play       │
│                                          │  12:04 ✓ volume 60        │
│  [estado: ● HABLANDO]                    │                           │
├──────────────────────────────────────────┴───────────────────────────┤
│  > tipeá acá para preguntar por texto (opcional)          q: salir   │ footer
└──────────────────────────────────────────────────────────────────────┘
```

- **Header:** estado de conexión (backend y ESP32 online por heartbeat), hora.
- **Panel conversación:** transcript en vivo con el estado actual resaltado.
- **Panel acciones:** log de acciones ejecutadas con ✓/✗ y timestamp.
- **Footer:** input de texto opcional y atajos de teclado.

## Catálogo de acciones v1 (contrato con `nube.md`)

| type | value | Ejecución |
|---|---|---|
| `media_key` | `play_pause` \| `next` \| `prev` | `pynput` → tecla multimedia |
| `volume` | `up` \| `down` \| `mute` \| `set:<n>` | Control de volumen del SO |
| `app` | `open:<nombre>` | `subprocess` con la app mapeada (spotify, chrome, terminal…) |
| `screenshot` | — | v2: captura + notificación |

La app mapea `value` → operación nativa del SO (documentar diferencias Windows/Linux/mac;
para la cátedra fijar un único SO de demostración).

## Estructura propuesta (`host/`)

```
host/
├── pyproject.toml
├── host/
│   ├── main.py          # bootstrap: conecta WS y lanza la TUI
│   ├── agent.py         # cliente WebSocket + ejecutor de acciones
│   ├── actions.py       # catálogo de acciones → implementación por SO
│   ├── ui.py            # app Textual (layout, bindings, refresco)
│   └── config.py        # URL del backend, token, mapa de apps
└── tests/
```

## Seguridad

- El host agent se autentica contra el backend con un **token por dispositivo**
  (`X-Host-Token`): es el único canal con permiso para ejecutar acciones en la PC.
- Nunca ejecutar comandos arbitrarios: solo el catálogo cerrado de acciones.
- Si llega una acción desconocida: loguearla en la TUI como ✗ y reportar al backend
  (no fallar en silencio).

## Entregables de S2

- [ ] `host/` con agent + TUI corriendo contra el orquestador de S1.
- [ ] Schema de acciones congelado en Fase 2 (junto con S1).
- [ ] Prueba automatizada del ejecutor de acciones (mock de backend → acción ejecutada).
- [ ] Config por SO documentada en `host/README.md`.
