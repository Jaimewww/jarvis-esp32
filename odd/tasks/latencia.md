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
- S10a (L16, user decisions for T11). The jarvis-input helper confirms by the action, never by
  words in the intent or the typed text:
  1. "Solo chat y correo": Return / ctrl+Return (any Return in a key sequence, or a `\n` in
     typed text) asks only in chat and mail apps. Chat/mail includes web chat and mail open in a
     browser, detected by the window title (e.g. WhatsApp, Telegram, Gmail, Outlook, Discord,
     Messenger, Slack). Return in a browser on any other page (search, forms) does not ask.
  2. "No pregunta nunca": typed text with words such as "borrar", "enviar" or "pagar" does not
     ask by itself; the intent is only the text shown in the confirmation prompt.
  3. "Sí, pregunta": clearing all content asks: `ctrl+a` followed by Delete / BackSpace /
     `ctrl+x`, and the Vim clear `g g d shift+g` (AGENTS.md).
  4. "Sí, pregunta": closing a window or app asks: `alt+F4`, `ctrl+w`, `ctrl+q`.
  Unchanged: the terminal and secrets blocklists, the lookalike-character denial, the 30 s
  timeout and deny-on-no-answer, and the prompt in the TUI.
- S11 (P5): Measure 2-3 local 3-4B models (llama.cpp or Ollama) against the same bench, for
  intent with parameters and short chat. Measure only; adopting one is a separate decision.
- S12: Remove merged worktrees and branches (pi-ctl, pi-e2e, pi-rev, pi-key, pi-next, and pi-stt
  after #13).

## Tasks

- [x] T1 S1 — #13: catch any transcriber error and emit exactly one turn_error, with a test. Route: inline (pi-stt). Commit 651496d on feat/stt-whisper (backend 54 passed, 6 skipped; RED observed first). Replied on #13.
- [x] T2 S2 — Review and approve #31/#32 with the two notes. Route: inline (gh). Both approved 2026-11-23.
- [x] T3 S3,S4 — Latency proposal PR with decisions A-D in docs/decisiones.md (D22). Route: inline. Commit eff5b6b, PR #33.
- [ ] T4 S5 — Per-stage turn timing plus `jarvis.bench` baseline. Route: gentle-ai-worker. Code done on feat/turn-timing (turntrace.py, bench.py with 14 fixed requests, PC/desktop behind --include-pc; host 240 passed, 6 skipped). Pending: baseline run with the user.
- [ ] T5 S5 — STT CPU-fallback warning. Route: inline.
- [x] T6 S4B,S6 — Define the basic catalog: docs/catalogo-basico.md (phrase table = test cases, with "no entran" rows). Route: inline.
- [ ] T7 S6 — P1 fast path in the backend. Route: Jaime (L5); we review.
- [x] T8 S7 — P3 model per request type. Closed by L12/L13: the user stays on OpenAI (ChatGPT), and no OpenAI model or thinking level beats gpt-6.1-sol low; no Claude comparison.
- [ ] T9 S8 — P4 first signal plus sentence TTS on Piper. Route: TBD (after #31/#32).
- [ ] T10 S9 — P2 composite tools, no screenshot by default, clock in the agent context. Route: inline, branch feat/pc-composite (worktree pi-comp, on feat/pc-media), PR #36: af91b89 (pc_write_in/pc_run, AGENTS.md, before_agent_start clock), f84ee4d (review advisories: no unmatched-window fallback, abort). Native review approved (risk+reliability, item 2); node tests 114 passed, test_install 201 passed. Installed live 2026-11-24. Pending: re-bench `question` + `pc` with the user.
- [ ] T11 S10 — Confirmation by action (security, own PR, native review). Route: explore the current confirmation rules → design for the user's approval → gentle-ai-worker, branch feat/confirm-by-action on feat/pc-composite.
- [ ] T12 S11 — P5 local AI measurement. Route: TBD.
- [ ] T13 S12 — Cleanup of worktrees and branches. Route: inline.
- [ ] T14 S5 — Find out why the bench's catalog and PC tools fail (L6), and make the bench flag a request that did not do its job (failed tool or no tool) as an error. Re-run the PC baseline. Route: gentle-ai-worker. Bench now has a `done k/n` column (53371d7); pc-terminal was replaced by pc-archivos (9443579); host 243 passed, 6 skipped. Pending: the user re-runs with `/control on`.
- [x] T15 S5,S6 — Add a `pc_media(action)` tool to jarvis-input/pc-control (wpctl/playerctl, needs no focused window), so volume and media work through the agent until P1 lands. Route: gentle-ai-worker, branch feat/pc-media (e9f1933, e53ad62, 6676f69 explicit mute/unmute). Native review approved (risk+reliability, item 2). Pending: live check after reinstall.

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
- L6 (2026-11-23, baseline, `python -m jarvis.bench --include-pc -n 3`, openai/gpt-6.1-sol
  thinking low, run by the user). Total median / worst, model calls:
  hora 12.08/12.62 (2), dia 10.59/11.56 (2), cuenta 4.83/6.32 (1), traduci 5.02/5.05 (1),
  capital 4.89/5.39 (1), spotify 14.51/15.77 (2), volumen-sube 10.41/14.31 (2),
  volumen-baja 10.89/11.81 (2), pausa 12.34/12.47 (2), siguiente 11.82/12.38 (2),
  navegador 11.58/12.48 (2), pc-vscode 10.76/11.18 (2), pc-buscar 10.31/10.43 (2),
  pc-terminal 5.28/5.29 (1). Human wait 0 everywhere. Findings from the first run of each:
  each model call takes 4-7 s (~5 s); tools take < 0.5 s; daemon echo 0.03-0.55 s; the first
  text arrives ~0.4 s before the end, so nothing is gained from streaming today. hora/dia
  spend one extra call on a `run` tool (the clock). The volume, pausa, siguiente and pc-vscode
  tools end `failed`, and pc-terminal used no tool. The bench reports 0 errors because it only
  counts protocol errors, so the catalog and PC rows do not measure a completed task yet
  (T14).
- L7 (evidence, T14): the PC permission was off during the run. jarvis-input logged `key
  denegado: sin permiso` x12 and `windows denegado: sin permiso` x3 (server.py:417-419), so
  volume, media and pc-vscode failed. There is no volume or media tool, and the agent improvised
  `pc_key XF86Audio*`. pc-terminal is refused by design (checks.py:217-238; pc-control.ts:147).
  `open` needs no permission, which is why spotify, navegador and pc-buscar worked.
- L8 (2026-11-23, second baseline with `/control on`, `-n 3`, /tmp/jarvis-baseline-2.json).
  Total median / worst, model calls, done:
  hora 11.03/12.52 (2) 3/3, dia 14.96/16.15 (2) 3/3, cuenta 5.33/5.51 (1) 3/3,
  traduci 5.13/6.11 (1) 3/3, capital 4.75/6.24 (1) 3/3, spotify 10.42/11.41 (2) 3/3,
  volumen-sube 11.20/14.78 (2) 0/3, volumen-baja 10.54/11.27 (2) 0/3, pausa 10.68/10.78 (2) 0/3,
  siguiente 12.09/13.08 (2) 0/3, navegador 11.47/13.23 (2) 3/3, pc-vscode 56.47/66.60 (4-6) 0/3,
  pc-buscar 11.40/12.24 (2) 3/3, pc-archivos 42.45/54.36 (7-8) 3/3.
  Causes from the jarvis-input journal:
  - The XF86Audio* keys go to the focused window, which was the ghostty terminal running the
    bench, so they were refused by the terminal blocklist (12 times). Media keys must not depend
    on focus (T15).
  - In pc-vscode, a `key` needed a jarvis-input confirmation, which is asked in the TUI and not
    in the bench's y/N. It went unanswered, was denied after ~30 s (3 times), and the bench shows
    wait 0. Until T11, keep the TUI visible during the bench and answer there.
  - pc-archivos takes 7-8 calls and ~42 s for an open-and-navigate task: the P2 target case.
- L9 (2026-11-23, live check of T15 after install.sh, bench `--include-pc --only catalog -n 3`,
  `/control` off): spotify 12.28/15.14 (2) 3/3, volumen-sube 10.51/12.77 (2) 3/3,
  volumen-baja 12.33/13.05 (2) 3/3, pausa 12.38/15.83 (2) 3/3, siguiente 12.75/13.24 (2) 3/3,
  navegador 14.43/14.66 (2) 3/3. pc_media fixes correctness (0/3 → 3/3). Each catalog order still
  costs ~10-15 s, two model calls of ~5-7 s each: only P1 (no model) reaches the 1.5 s goal.
- L10 (2026-11-23, user): "ya no usare openia usare models de claude". P3 compares Claude models
  (Haiku 5.5 for chat, Sonnet 5.5 for PC tasks, Opus 5.5 as reference) instead of gpt-6.1-sol;
  the OpenAI baseline (L8/L9) remains the "before" reference.
- L11 (2026-11-23, user): the daemon offers only OpenAI for now (Anthropic needs an API key; the
  Claude subscription is refused), so "hasta solucionar usemos OPENIA". P3 compares OpenAI
  models first: gpt-6.1-sol low (current), gpt-6-luna off, gpt-6.1-terra off, gpt-5.4-mini off,
  gpt-5.4-nano off. Claude models get the same bench once a key is connected.
- L12 (2026-11-23, P3 bench, `--only question -n 3`, total median s for hora/dia/cuenta/traduci/capital):
  gpt-6.1-sol low 12.26/12.99/7.11/5.41/5.77; gpt-6-luna off 13.18/13.72/4.39/5.61/6.87;
  gpt-6.1-terra off 14.19/12.26/6.71/5.47/6.64. gpt-5.4-mini off and gpt-5.4-nano off failed
  every run in ~1.2 s with "Something went wrong while answering" (probably not served by this
  account; not investigated). Conclusion: with OpenAI, turning thinking off or picking a lighter
  model gains nothing; each call costs ~5 s regardless. P3 stays open for Claude (Haiku) once a
  key is connected. Real wins remain fewer calls: P1, P2, and giving the agent the current date
  and time so hora/dia stop spending an extra call on the clock (~12 s → ~5 s).
- L13 (2026-11-24, user): "seruire usando chatgpt ya nada de claude. asi que continua con el plan".
  L10 is withdrawn: the agent stays on OpenAI (gpt-6.1-sol low). P3 closes with the L12 result;
  latency work continues with fewer calls (T10 P2 next).
- L14 (2026-11-24, T10 bench after installing PR #36, `--include-pc --only question --only pc -n 3`,
  gpt-6.1-sol low). Total median / worst, calls, done: hora 16.81/34.03 (1) 3/3, dia 16.82/27.40 (1)
  3/3, cuenta 12.32/18.10 (1), traduci 13.33/13.58 (1), capital 13.55/19.41 (1), pc-vscode
  55.40/60.29 (4-5) 3/3, pc-buscar 18.02/27.20 (2) 3/3, pc-archivos 41.65/42.09 (4) 3/3.
  Calls dropped as intended: hora/dia 2 → 1 (the clock works), pc-archivos 7-8 → 4. pc-vscode
  did not drop (4-6 → 4-5). But each call now costs ~10-13 s instead of ~5 s, including
  one-call questions with no PC tools (cuenta 5.33 → 12.32), so the totals did not improve.
  Pending: per-call timings (`--json`) to tell provider latency from a T10 side effect.
- L15 (2026-11-24, user): "eso de medicion mejor ahgamolo cuando jaime haga sus cambios. lo
  siguiente si hagamos". The per-call bench waits for P1 (T7); T11 starts now. Jaime was sent
  the P1 request by WhatsApp.
- L16 (2026-11-24, user, T11 decisions): Enter asks in "Solo chat y correo"; risky words in
  typed text: "No pregunta nunca (Recomendado)"; clear all content: "Sí, pregunta
  (Recomendado)"; close window/app (Alt+F4, Ctrl+W, Ctrl+Q): "Sí, pregunta (Recomendado)".
  Agent decisions: web chat/mail in a browser counts as chat/mail by window title, so WhatsApp
  Web or Gmail in Brave still ask on Enter; a `\n` in typed text counts as Enter; the rule
  table lives in code. Recorded as S10a.
- L17 (2026-11-24, evidence, T11): commits d4f92f6 (code+tests) and b6b2990 (docs, D23) on
  feat/confirm-by-action. Native review approved (risk+reliability, item 2) with advisories;
  independent verify PASS on S10a 1-4 and Unchanged (222 helper tests, 114 node tests). Residual
  gaps in the same intent, to harden in a follow-up commit (agent decision, within S10a):
  (a) rules see one call only: `ctrl+a` then `Delete` in the next call, `g g` then `d shift+g`,
  or `ctrl+a` then pc_type over the selection clear with no prompt; (b) typed text "ggdG" or
  ":%d" in Vim normal mode clears with no prompt; (c) the browser title is read once before
  focusing: a tab switch inside the sequence (`ctrl+Tab Return`, `ctrl+2 Return`) or a title
  change before wtype can reach web chat without a prompt; (d) more equivalents: `Tab space` to
  press Send in chat/mail, `ctrl+a KP_Delete`, `ctrl+Home ctrl+shift+End Delete`, `ctrl+F4`,
  and the niri binds `super+w`, `super+shift+e`, `ctrl+alt+Delete` (close/quit); (e) no server
  test proves the target window's title is used; e2e `words_back_to_back` ignores the reply;
  host/README.md:410 still says "las palabras de riesgo". Not changed (fail-safe or user
  choice): common words in the title pattern over-ask; web chats outside the title list
  (Google Chat, LinkedIn) do not ask.
