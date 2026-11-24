# Latency plan: unblock PR #13 and cut turn time

This doc tracks the approved latency plan from docs/latencia-propuestas.md (P1-P5) and the
work needed to unblock Jaime's review. Branch `feat/latency` (worktree `pi-lat`). The #13 fix
lands on `feat/stt-whisper` (worktree `pi-stt`).

## Specs

- S1 (Jaime, PR #13): "En `backend/app/main.py:251–266` solo se captura `WhisperServerError`.
  Si el transcriptor falla con otra excepción, ya se emitió `turn_started` y el turno puede
  quedar sin evento de cierre. ¿Podés cubrir ese camino con un test que verifique un único
  `turn_error` y ningún `turn_done`?" Agreeing on the STT `502` is deferred to the separate
  C1/C2 update.
- S2: Review Jaime's Piper chain (#30 → #31 → #32). Approve #31/#32. Leave two non-blocking
  notes: `tools/probe-piper.sh:17` `ffmpeg -n` fails on a second run to the same path, and the
  README link pinned to `blob/036a6d1`. Merge order: #13 first, then Jaime rebases his chain.
- S3: Publish the latency proposal (docs/latencia-propuestas.md, without the PDF) as a PR and
  record decisions A-D (L2) in docs/decisiones.md.
- S4 (L2, decisions): A "que viva en el back": the fast path lives in the backend. B "tendiramos
  que probar las cosas basicas o ver que seria lo basico, que abarca": the initial catalog covers
  the basic commands, defined and tested first. C "si acepto": accept composite tools, no
  screenshot by default, and confirmation by action. D "si hagamos medicion": run the local AI
  measurement (P5).
- S5 (Measure first): Time every stage of a whole turn: end of speech, STT, recognition, each
  model call, each tool, first signal, end. Add `python -m jarvis.bench`: a fixed set of 10-15
  requests (catalog commands, short questions, 3 PC tasks) reporting the median and the worst
  case. Warn in the log and the TUI when STT runs on CPU (Jaime measured 14.85 s on CPU vs
  ~350 ms on Vulkan GPU).
- S6 (P1): A phrase recognizer in the backend with no AI, which runs before the agent. When it
  recognizes the phrase it executes directly; otherwise it passes to the agent with no delay.
  Target: "abrí Spotify" ~4.9 s → ~0.5-1 s.
- S7 (P3): Use the fastest model that is good enough for chat, and sol for long tasks. Measure
  the cost of switching models in the shared daemon (D14). Target: short question ~3.7 s →
  ~2.6 s.
- S8 (P4): Play a short sound and light when the user stops speaking (< 0.5 s). Speak the reply
  sentence by sentence through Piper (Jaime's V2). Long tasks get an immediate "Dale, lo hago".
- S9 (P2): Add composite tools `pc_write_in(app, texto)` and `pc_run(app, teclas)`. Take no
  screenshot after each action unless something failed or the user asked. Target: PC task ~48 s
  with 7 calls → ~10-15 s with 2.
- S10 (P2): Confirm by the action, not by words of the intent. This is a security guard: it goes
  in its own PR with desktop e2e tests and native review.
- S11 (P5): Measure 2-3 local 3-4B models (llama.cpp or Ollama) against the same bench, for
  intent with parameters and short chat. Measure only; adopting one is a separate decision.
- S12: Remove merged worktrees and branches (pi-ctl, pi-e2e, pi-rev, pi-key, pi-next, and pi-stt
  after #13).

## Tasks

- [x] T1 S1 — #13: catch any transcriber error and emit exactly one turn_error, with a test. Route: inline (pi-stt). Commit 651496d on feat/stt-whisper (backend 54 passed, 6 skipped; RED observed first). Replied on #13.
- [x] T2 S2 — Review and approve #31/#32 with the two notes. Route: inline (gh). Both approved 2026-11-23.
- [x] T3 S3,S4 — Latency proposal PR with decisions A-D in docs/decisiones.md (D22). Route: inline. Commit eff5b6b, PR #33.
- [ ] T4 S5 — Per-stage turn timing plus `jarvis.bench` baseline. Route: TBD.
- [ ] T5 S5 — STT CPU-fallback warning. Route: inline.
- [x] T6 S4B,S6 — Define the basic catalog: docs/catalogo-basico.md (phrase table = test cases, with "no entran" rows). Route: inline.
- [ ] T7 S6 — P1 fast path in the backend. Route: Jaime (L5); we review.
- [ ] T8 S7 — P3 model per request type. Route: TBD.
- [ ] T9 S8 — P4 first signal plus sentence TTS on Piper. Route: TBD (after #31/#32).
- [ ] T10 S9 — P2 composite tools, no screenshot by default. Route: TBD.
- [ ] T11 S10 — Confirmation by action (security, own PR, native review). Route: TBD.
- [ ] T12 S11 — P5 local AI measurement. Route: TBD.
- [ ] T13 S12 — Cleanup of worktrees and branches. Route: inline.

## Log

- L1 (2026-11-23, user): "arma un plan, incuido lo de bajhas la latencia y tiempos, abre el pdf
  para leer mientras armas el plan, termino de leer y veo si apruebo el plan"
- L2 (2026-11-23, user, plan approved): "dale, que viva en el back, b tendiramos que probar las
  cosas basicas o ver que seria lo basico, que abarca, si acepto, y si hagamos medicion"
- L3 (evidence): Today's backend `/converse` (feat/stt-whisper) only does STT. The reply is a
  placeholder ("En orden, señor.") and so is the action (play_pause). The agent is not wired to
  the backend; the TUI talks to the daemon directly.
- L4 (evidence): D21 assigns the fast path and the catalog to S1 (Jaime, `backend/`). T6/T7
  need his agreement, or an explicit handover, before code.
- L5 (2026-11-23, user): basic catalog = multimedia (pausá/seguí/siguiente/anterior), volume
  (subí/bajá/silencio), "abrí X" with a closed list, and "qué hora es"/"qué día es". P1 is
  written by "Jaime, con la lista y los tests que armemos".
