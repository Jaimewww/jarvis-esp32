# Gentle Dot: reglas y control de la PC (S13, S14)

Lo que esta carpeta le agrega al agente del daemon de Gentle Dot (motor
`@earendil-works/pi-coding-agent` 1.5.0). Lo instala `host/systemd/install.sh`.

| Archivo | Se instala en |
| --- | --- |
| `AGENTS.md` | `<agente>/AGENTS.md` (reglas del agente) |
| `extensions/pc-control.ts` | `<agente>/extensions/jarvis-pc-control/index.ts` |
| `extensions/pc-control-core.ts` | `<agente>/extensions/jarvis-pc-control/pc-control-core.ts` |
| `../bin/jarvis-stop` | `~/.local/bin/jarvis-stop` (`--bin-dir`) |

`<agente>` es `~/.gentle-dot/agent` (o `--agent-home` / `GENTLE_DOT_AGENT_HOME`). El motor
carga solo `extensions/*/index.ts` al arrancar, así que hay que reiniciar el servicio
(`install.sh --enable`, o `systemctl --user try-restart gentle-dot.service`).

## Arquitectura: el ayudante es la barrera

```text
TUI de Jarvis ── control.sock ──┐                 ┌── wtype / grim / niri msg
jarvis-stop  ──  (grant/revoke, │  jarvis-input   │   gtk-launch / xdg-open
                  confirmaciones)│  (único con     │
agente (pc_*) ── agent.sock ────┘   la pantalla)  └── (argv, sin shell)
```

- **`jarvis-input`** (`host/input/`, servicio de usuario en `graphical-session.target`) es el
  único proceso con acceso a Wayland y niri. Guarda el permiso de `/control on` en memoria
  (reiniciarlo = permiso revocado) y hace todos los controles de S12: lista de bloqueo por
  ventana enfocada, re-chequeo de foco, palabras de riesgo con confirmación en la TUI (30 s),
  10 acciones/s, 2000 caracteres.
- **`gentle-dot.service`** corre aislado: sin Wayland, X11, niri, bus de sesión ni
  `jarvis-control/`; extensiones y ayudante de solo lectura (`host/systemd/`).
- **Esta extensión** es un cliente fino de `$XDG_RUNTIME_DIR/jarvis/agent.sock`: un JSON por
  línea de ida y uno de vuelta, 45 s de límite (deja lugar a la confirmación de 30 s), y la
  señal de cancelación del agente cierra el socket. No lee archivos de permiso ni llama a
  niri, `wtype` o `grim`. Si el ayudante responde `{"ok":false,"error":"…"}`, la herramienta
  falla con ese mensaje; si no hay socket, con `jarvis-input is not running …`.
- **El hook `tool_call` no es la barrera**: es defensa en profundidad por texto (el agente
  podría esquivarlo). Bloquea `bash` que invoque `wtype`, `grim`, `ydotool(d)`, `dotool(c)`,
  `wlrctl`, `niri msg … action`, `systemd-run` o `systemctl --user`, o que nombre
  `jarvis-control`, `jarvis/agent.sock`, `~/.local/share/jarvis`, las extensiones o el
  `settings.json` del agente; y `write`/`edit` sobre esas rutas.

## Herramientas

| Herramienta | Pedido en `agent.sock` | Permiso |
| --- | --- | --- |
| `pc_list_apps{query?}` | `{"op":"apps"}` → `apps:[{id,name}]`; la extensión filtra por `query` (nombre o id, sin mayúsculas ni tildes) y devuelve "nombre — id", máx. 60 líneas; sin coincidencias, la lista entera | no |
| `pc_open_app{app}` | `{"op":"open","app":"brave-browser"}` (desktop id) | no (etapa a) |
| `pc_list_windows{}` | `{"op":"windows"}` → `windows:[{id,app_id,title,focused}]`; devuelve "id · app_id · título · (focused)" | `/control on` |
| `pc_focus_window{window_id}` | `{"op":"focus","window_id":42}` (entero; se niega en terminales y en la lista de bloqueo) | `/control on` |
| `pc_open_url{url}` | `{"op":"open","url":"https://…"}` | no (etapa a) |
| `pc_type{text,intent}` | `{"op":"type","text":…,"intent":…}` | `/control on` |
| `pc_key{keys,intent}` | `{"op":"key","keys":"ctrl+l","intent":…}` | `/control on` |
| `pc_screenshot{scale?}` | `{"op":"screenshot","scale":0.5}` (0,1–1) → `png_base64` | `/control on` |

Los errores del ayudante (`{"ok":false,"error":"…"}`) vuelven tal cual como error de la
herramienta. Para teclear, `AGENTS.md` fija el flujo (S14): ventana con `pc_list_windows` (o
`pc_list_apps` → `pc_open_app` → `pc_list_windows`), `pc_focus_window`, y recién ahí
`pc_type`/`pc_key`; nunca en una terminal (la TUI corre en una).

## Parada de emergencia

`jarvis-stop` manda `{"op":"revoke"}` a `$XDG_RUNTIME_DIR/jarvis-control/control.sock` (la
llave principal), después `{"type":"abort"}` al daemon (`ws://127.0.0.1:${GENTLE_DOT_PORT:-4317}/ws`,
hello con protocolo 1 y el token leído de `${GENTLE_DOT_DATA_DIR:-~/.gentle-dot}/token`, que
nunca imprime) y, de respaldo, `pkill -x wtype`. Si el ayudante o el daemon no están, lo dice
y sigue (sin ayudante no hay permiso activo). Siempre sale con 0; si hay `notify-send`, avisa
en el escritorio (`JARVIS_STOP_NOTIFY=0` lo apaga).

El atajo se agrega a mano (ni `install.sh` ni nadie edita tu configuración de niri). En
`~/.config/niri/binds.kdl` (enlazado a `~/dotfiles/config/niri/`), dentro de `binds { }`:

```kdl
Alt+Shift+Escape { spawn "jarvis-stop"; }
```

Si `~/.local/bin` no está en el PATH de niri, usa la ruta completa
(`spawn "/home/<usuario>/.local/bin/jarvis-stop"`). niri recarga la configuración solo.

## Pruebas

```sh
node --test host/gentle-dot/tests/*.test.ts   # Node 24+; cliente contra un agent.sock falso
bash host/bin/test_jarvis_stop.sh            # control.sock y daemon falsos, sin procesos reales
bash host/systemd/test_install.sh            # instalador en un sandbox
```

### Prueba manual en la máquina

1. Instalar y arrancar (`host/systemd/install.sh … --enable`); confirmar que
   `jarvis-input.service` y `gentle-dot.service` están activos y que existen
   `$XDG_RUNTIME_DIR/jarvis/agent.sock` y `<agente>/extensions/jarvis-pc-control/index.ts`.
2. "Abre Brave" → el agente usa `pc_open_app{app:"brave-browser"}`, Brave abre y responde que
   abrió (sin `bash`). "Abre https://example.com" → `pc_open_url`.
3. Sin permiso: "escribe hola" → el agente dice que hace falta `/control on` en la TUI.
4. "Ejecuta wtype hola con bash" → bloqueado, sugiere `pc_type`. (Aunque lo esquivara, el
   daemon no tiene acceso a la pantalla: falla.)
5. `/control on` en la TUI; enfocar un editor y pedir "escribe hola" → aparece.
6. `pc_key` con `ctrl+l` en el navegador → selecciona la barra de direcciones.
7. "Saca una captura de pantalla y dime qué ves" → describe la pantalla.
8. "Escribe: envía el correo" → la TUI pide confirmación; no contestar en 30 s → no se escribe.
9. Enfocar KeePassXC (u otra de la lista) y pedir escribir → se niega.
10. `systemctl --user stop jarvis-input` (desde tu terminal) y pedir "abre Brave" → el agente
    dice `jarvis-input is not running`.
11. (S14) "Abre notas y escribe hola mundo" con control encendido → `pc_list_apps{query}`
    encuentra `dev.duxlabs.shell.notepad`, `pc_open_app`, `pc_list_windows`,
    `pc_focus_window` sobre la ventana del bloc y `pc_type` → "hola mundo" aparece ahí, no en
    la terminal de la TUI.
12. (S14) Con el bloc ya abierto: "escribe hola" → el agente enfoca el bloc antes de escribir
    (no responde "la ventana activa es una terminal").
13. (S14) "Enfoca la terminal y escribe ls" → `pc_focus_window` se niega; el agente lo dice y
    no intenta otra vía.
14. (S14) Sin permiso: "¿qué ventanas tengo abiertas?" → pide `/control on`; "¿qué apps
    tengo?" → `pc_list_apps` funciona igual.
15. Con control encendido, pulsar `Alt+Shift+Escape` durante una tarea → `jarvis-stop` dice
    "permiso de control revocado", la tarea se corta y la siguiente llamada `pc_*` se niega.
