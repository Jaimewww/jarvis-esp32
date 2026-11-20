# Pista S2, segunda vuelta: login en la TUI, instalador, agilidad, Whisper y docs

Ideas sueltas del usuario del 2026-11-20 (L1), ordenadas en tareas. Cada tarea va en su
propia solicitud desde `main`; worktree `jarvis-esp32-worktrees/pi-next`.

## Specs

- S1 (L1): "Que la TUI de Jarvis maneje /login, para conectar la cuenta sin ir al
  navegador." Protocolo del daemon: `auth_list`, `auth_login`, `auth_reply`,
  `auth_logout` / `auth_providers`, `auth_prompt` (`text|secret|select|manual_code`),
  `auth_event` (`info|auth_url|device_code|progress`), `auth_done`. El secreto nunca se
  registra ni se guarda.
- S2 (L1): "Corregir lo que encontró la revisión del instalador de systemd: no reintenta
  después de un daemon-reload fallido; no tiene límite de arranques; no revisa si el
  checkout tiene cambios sin commitear; no hace backup."
- S3 (L1): "Poner al día tu checkout local de main, que sigue en la PR #1, sin tocar tus
  bocetos de design/."
- S4 (L1): "Borrar los worktrees de ramas ya fusionadas, como pi-ci y pi-agent." Sin
  perder lo no versionado de `pi` (`design/**/.preview`, `docs/guia-pantalla/`).
- S5 (L1, L2): "creo que se demora mucho en responder, tenemos que hallare el equilibro";
  "osea creo que el modelop mucho piensa, quiero mas agilidad en las respuestas".
- S6 (L1, L2): "dejes listo para comenzar a probar con el whisper local que tengo"; "lo
  hago yo y le aviuso a jhaime por whastapp" (tarea V1 de la pista de voz, solicitud
  aparte); "Instalar la versión con CUDA ya" (whisper.cpp 1.9.5 hoy es solo CPU; RTX 4050
  6 GB; modelo `~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin`).
- S7 (L1, L2): "documentes como estamos ahciendo todo en quarto" — "Sitio Quarto en docs/
  (HTML)".
- S8 (L1): "actualices el readme que quede super visual". `README.md` es compartido: va en
  su propia solicitud.

## Tasks

- [x] T1 (S3): `main` local adelantado a `afa7124` con `--ff-only`; `design/` intacto.
- [x] T2 (S4): rescatar lo no versionado de `pi` al checkout principal, entornos nuevos
  en `pi-next`, quitar `pi` y `pi-agent` y sus ramas fusionadas. `pi-ci` ya quitado. Inline. Hecho: copia verificada con `diff -rq`, ramas locales fusionadas borradas (queda `docs/guia-pantalla`, sin fusionar).
- [x] T3 (S2): instalador de systemd: reintento del `daemon-reload`, límite de arranques
  (ya existe en la unidad: fijarlo con una prueba), revisión de checkout sucio, backup de
  la unidad previa. `host/systemd/`. Rama `fix/systemd-installer`. Worker. Evidencia L5.
- [x] T4 (S1): `/login` en la TUI. `host/jarvis/`. Commits `d361a3a`, `886bf7a`, fusión `0c2c810`; solicitud #14. Evidencia L7.
- [x] T5 (S5): medir el camino escrito (eco, primer texto, fin) y bajar el razonamiento o
  elegir un modelo más ágil vía el daemon. `host/`, `gentle_dot.py`. Commit: pendiente.
- [ ] T6 (S6): whisper.cpp con CUDA en el sistema, `whisper-server` como servicio de
  usuario con el modelo cargado una vez, medición con un WAV en español. Inline.
- [ ] T7 (S6): V1 — transcriptor real detrás de `Transcriber` en `/converse`, con el tiempo
  de la etapa `stt`. `backend/`. Solicitud aparte para Jaime. Commit: pendiente.
- [x] T8 (S7): sitio Quarto en `docs/` sobre cómo trabajamos (ODD, pistas, worktrees, CI,
  solicitudes). Commit: pendiente.
- [x] T9 (S8): `README.md` visual. Solicitud propia. Commit: pendiente.

## Log

- L1 (usuario, 2026-11-20): " 1. Que la TUI de Jarvis maneje /login, para conectar la
  cuenta sin ir al navegador.
   2. Corregir lo que encontró la revisión del instalador de systemd:
       - no reintenta después de un daemon-reload fallido;
       - no tiene límite de arranques;
       - no revisa si el checkout tiene cambios sin commitear;
       - no hace backup.
   3. Poner al día tu checkout local de main, que sigue en la PR #1, sin tocar tus bocetos
   de design/.
   4. Borrar los worktrees de ramas ya fusionadas, como pi-ci y pi-agent. . hagamos esto y
   adicional creo que se demora mucho en responder, tenemos que hallare el equilibro y
   luego quiero que dejes listo para comenzar a probar con el whisper local que tengo. de
   igual amnera documentes como estamos ahciendo todo en quarto y actualices el readme que
   quede super visual, son ideas sueltas, asi que ordenalas y arma el plan"
- L2 (usuario, respuestas): demora: "osea creo que el modelop mucho piensa, quiero mas
  agilidad en las respuestas"; Whisper: "lo hago yo y le aviuso a jhaime por whastapp";
  GPU: "Instalar la versión con CUDA ya"; Quarto: "Sitio Quarto en docs/ (HTML)".
- L3 (evidencia): `/converse` sigue siendo un simulacro (`backend/app/main.py:213-253`);
  `turn_stage` pendiente; `gentle_dot.py` no fija modelo, lo decide la cuenta del daemon.
  whisper.cpp 1.9.5 y ggml 0.29.0 sin backend CUDA (`/usr/lib/ggml/` solo CPU); no hay
  `whisper-server` corriendo. Quarto 1.11.9 en `~/.local/bin`. La unidad ya trae
  `StartLimitIntervalSec=10min` y `StartLimitBurst=10` (`gentle-dot.service.in:18-19`).
- L4 (evidencia T6): `extra/ggml-cuda 0.29.0-2` existe (depende de `cuda`, `nccl`); instalar
  necesita la contraseña de sudo del usuario.
- L5 (evidencia T3): `test_install.sh` 68 pasan y 0 fallan (eran 38); RED con 8 fallos antes
  del cambio. Marcador `gentle-dot.service.reload-pending` para reintentar el
  `daemon-reload` (también en `--uninstall`), `--allow-dirty` frente a `git status
  --porcelain`, copia `gentle-dot.service.bak`, límite de arranques fijado por prueba. Sin
  shellcheck (no instalado); no probado contra el systemd real. El checkout de Gentle Dot
  tiene `.atl/` sin seguimiento (caché de agente del 2026-11-18): con la nueva revisión,
  reinstalar exige borrarlo o `--allow-dirty`.
- L6 (evidencia T9, T8): README visual en la solicitud #10; sitio Quarto en `docs/sitio/` en la
  #11 (`quarto render docs/sitio` sin advertencias, 4 páginas). Diagramas Mermaid sin mirar en
  navegador.
- L7 (evidencia T4): verificador independiente (riesgo 2, credenciales) falló dos veces: la
  clave quedaba visible tras `Esc` y podía salir como chat; un corte dejaba el flujo trabado;
  un flujo viejo trababa `/login`; `Ctrl+C` copiaba la clave oculta. Todo corregido con
  pruebas; veredicto final PASS en `0c2c810`. Host 143 pasan y 5 omitidas. Pendiente menor:
  `Esc` doble antes de que el daemon nombre el flujo, si ese flujo termina sin tramas, hace
  que el siguiente `/login` se cancele solo (sale con `Esc` doble).
- L8 (evidencia T5, solicitud #12): medianas con "Responde solo: ok", N=3: gpt-6.1-sol medium
  5,07 s; sol low 3,36 s; gpt-6.1-terra low 2,88 s; gpt-6-luna off 2,57 s; eco 0,03 s; gpt-5.x
  falla en esta cuenta ("Something went wrong while answering"). El usuario eligió "gpt-6.1-sol
  en low (3,4 s)"; aplicado con `python -m jarvis.bench --model openai/gpt-6.1-sol --thinking
  low` (mediana medida al aplicar 3,97 s, una corrida de 5,3 s).
- L9 (evidencia T7, solicitud #13 abierta para Jaime): `WhisperServerTranscriber` con
  `JARVIS_STT=whisper`, `turn_stage` de `stt` y `turn_error` con 502. Backend 53 pasan y 6
  omitidas. En CPU, unos 15 s por clip; sin WAV con voz real en español. Dos revisiones
  nativas aprobadas con hallazgos no bloqueantes (excepciones fuera de `WhisperServerError`
  dejan el turno abierto; `JARVIS_STT_TIMEOUT_S` sin validar).
