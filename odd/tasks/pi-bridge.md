# Pi bridge: Gentle Dot's protocol on top of Pi

A local daemon in `host/` that speaks the Gentle Dot WebSocket protocol v1 to Jarvis's clients
(TUI, bench, backend adapter) and drives one `pi --mode rpc` process underneath. The repo keeps
Gentle Dot as the default; a machine opts into the bridge. Branch `feat/pi-bridge` (worktree
`pi-bridge`).

## Specs

- S1 (L1, L2, L3, goal): "dejalo gentle dot por defecto pero que en realidad funciona pi por
  default". Chosen design (L3): "Puente con protocolo de Gentle Dot": a new daemon in `host/`
  that speaks the current protocol and drives Pi inside; the TUI, the bench and the backend do
  not change; on the user's machine it replaces the Gentle Dot service.
- S2 (no client change): `host/jarvis/client.py` (`JarvisClient`, used by the TUI and by
  `python -m jarvis.bench`) and `backend/app/agents/gentle_dot.py` (`GentleDotAgent`) must work
  against the bridge with no edit to those files. The protocol is whatever those two clients
  send and parse today; `backend/tests/fake_gentle_dot.py` is a reference fake of the daemon.
  The bridge listens on `127.0.0.1` only, default port 4317, both overridable.
- S3 (auth): a client must send `hello` with the right token and protocol version before
  anything else, exactly as the clients do today. The bridge accepts the token the clients
  already read (default `~/.gentle-dot/token`, path overridable); it creates the file with mode
  0600 when it is missing and never logs or prints the token. A wrong or missing token and a
  malformed first frame close the connection with the close codes the clients already map
  (4401 and 4400). The comparison is constant-time.
- S4 (a turn): `send{text}` runs one Pi prompt. The bridge emits the frames the clients expect
  for a turn, in the order they expect: the `user_message` echo, `agent_state` changes,
  `message_delta` for each text delta, `message_done` with the whole text, and `error` when Pi
  reports one. `abort` stops the running turn and the next `send` works. One turn at a time, as
  today (D14); what the daemon does today with a `send` during a running turn is what the
  bridge does.
- S5 (Pi process): the bridge starts `pi --mode rpc --no-session` with no discovered
  extensions, skills, prompt templates or context files, loads the Jarvis extension
  (`pc-control.ts`) and the Jarvis agent rules (`AGENTS.md`) explicitly, and takes the model
  (default `openai/gpt-6.1-sol` with thinking `low`) and extra extension paths from its
  configuration. If Pi exits, the running turn ends with an `error` frame and the next `send`
  starts Pi again. Measured on 2026-11-25 (L4): this command line answers a short question in
  about 2 s and loads `pc-control.ts` unchanged.
- S6 (tools): each Pi `tool_execution_start` / `tool_execution_end` becomes the `activity`
  frames the clients use (running, then done or failed), so the TUI shows them and
  `turntrace` / the bench count tools and model calls as they do with Gentle Dot.
- S7 (questions): a Pi `extension_ui_request` that needs an answer (confirm, select, input)
  becomes an `ask` frame; the client's `ui_response` is sent back to Pi as
  `extension_ui_response`; `ask_resolved` goes to every client. With no client connected the
  request is denied.
- S8 (models): `models_list` answers from Pi's `get_available_models` in the `models` frame
  shape the clients parse, and `model_set` (model and thinking level) is applied with Pi's
  `set_model` / `set_thinking_level` and broadcast.
- S9 (several clients and history): every connected client receives the turn frames. The
  bridge keeps the current conversation in memory and answers `get_history` and
  `list_conversations` with one conversation, enough for the TUI to open and show it; the
  account frames report a ready account so no login view appears. Requests the clients send
  that the bridge does not support get the protocol's `error` answer, never silence.
- S10 (install and docs): a systemd user unit and installer step for the bridge with the same
  hardening as `host/systemd/gentle-dot.service.in` plus write access to `~/.pi` (L4: Pi fails
  under `ProtectHome=read-only` on `~/.pi/agent/trust.json.lock`). Gentle Dot stays the
  default; enabling the bridge is an explicit option and the two never run together on the same
  port. `host/README.md` and `docs/decisiones.md` describe it (a new decision entry).
- S11 (proof on the real machine): with the bridge running, `python -m jarvis.bench` (questions
  and the volume requests, `-n 3`) completes with every run done, and the TUI opens, shows a
  reply and a tool activity. Compare against the 2026-11-25 numbers in L4.

## Tasks

- [x] T1 S2,S3,S4,S5 — Bridge core: server, auth, one turn with deltas, abort, Pi process
  lifecycle, tested against a fake Pi process and with the unchanged `GentleDotAgent`. Route:
  gentle-ai-worker. Commits 715494e (hello and token guard), 6295ce9 (turns on the Pi child)
  and the hardening fix after verify (L5). Host 291 passed, 6 skipped; backend 82 passed, 7
  skipped; pyright 0 errors on `host/jarvis/pibridge`. Independent verify: S2-S5 pass (L5).
- [ ] T2 S6,S7,S8,S9 — Activities, questions, models, several clients and history, tested with
  the unchanged `JarvisClient`. Route: gentle-ai-worker, after T1.
- [ ] T3 S10 — systemd unit, installer, README and decision entry. Route: gentle-ai-worker,
  after T2.
- [ ] T4 S11 — Run the bench and the TUI against the bridge on the user's machine; record the
  numbers. Route: inline with the user.

## Log

- L1 (2026-11-25, user): "oye hoy vi una app se llama gentle stream y usa el mdoelo default de
  gentle shell, que tan viable es cambiar gentle dot por shell, que esta mucho mas trabajado, y
  no me pongo en esos problemas de suscripciones"
- L2 (2026-11-25, user): "dejalo gentle dot por defecto pero que en realidad funciona pi por
  default"
- L3 (2026-11-25, user, choice between a bridge, a backend adapter, or a plan only): "Puente
  con protocolo de Gentle Dot (recomendado)". Reason for the bridge: `/converse` does not route
  to any agent yet (`backend/app/main.py:247`, "agent routing is not wired here yet"), so a
  backend adapter would have no caller; the daemon is used today by the TUI and the bench.
- L4 (2026-11-25, evidence, spike outside the repo with Pi 1.6.1): `pi --mode rpc --no-session
  -ne -ns -np -nc --no-themes --model openai/gpt-6.1-sol:low -e
  host/gentle-dot/extensions/pc-control.ts --system-prompt <short Jarvis prompt>
  --append-system-prompt host/gentle-dot/AGENTS.md`. RPC is JSON lines on stdio: commands
  `prompt`, `abort`, `new_session`, `set_model`, `get_available_models`, `get_state`; events
  `agent_start`, `turn_start`, `message_update` (`assistantMessageEvent.type == "text_delta"`,
  `.delta`), `tool_execution_start` / `tool_execution_end` (`toolCallId`, `toolName`, `args`,
  `result`, `isError`), `extension_ui_request`, `agent_end`. Same day, same model, n=3
  medians: Gentle Dot questions 6.59-7.41 s, volume 15.96 / 14.10 s, Spotify 13.80 s; Pi
  questions 1.77-2.31 s, volume 4.13 / 4.25 s, Spotify 4.80 s. Pi stayed flat over 60 turns.
  `new_session`, `abort` (stops in 0.01 s) and `set_model` work. Under the daemon's systemd
  hardening Pi needs `ReadWritePaths=%h/.pi`. The Claude subscription models need the extra
  extension `~/.pi/agent/npm/node_modules/pi-claude-bridge`.
- L5 (2026-11-25, evidence, T1). Smoke with the real `pi` on port 4391 and a temporary token
  file: a question and volume up / down completed (5.25 s and 4.36 s for the tool turns), no
  orphan process. Independent verify on 715494e + 6295ce9: S2-S5 pass, no blocker; about 20
  bad first frames closed with 4401 / 4400 / 1009 and leaked no frame; the token never reached
  the logs. Fixed afterwards, RED first: a lone-surrogate token closed with 1011 (now 4401), no
  warning on a bind outside loopback, no warning on a token file other users can read. Open
  notes: no `Origin` check (the token is still required); a slow client holds the broadcast
  lock; unsupported frames are dropped silently until T2 (S9). Protocol fact that T2 must keep:
  Gentle Dot emits an empty `message_done` for an assistant message that only calls tools
  (`host/jarvis/turntrace.py:4-5`) and the bridge does the same. Follow-up outside this feature
  (backend, Jaime): `GentleDotAgent.reply` returns at the first `message_done`, so a tool turn
  gives it an empty reply with either daemon.
