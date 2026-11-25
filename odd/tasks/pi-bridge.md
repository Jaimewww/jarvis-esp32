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
- S10 (install and docs; the write access was narrowed by L9): a systemd user unit and
  installer step for the bridge with the same hardening as
  `host/systemd/gentle-dot.service.in`. The bridge's Pi uses an agent directory of its own
  (`PI_CODING_AGENT_DIR`), the only Pi path the unit may write; the user's `~/.pi` stays
  read-only for the service (L4: Pi fails under `ProtectHome=read-only` when it cannot write
  its agent directory, `trust.json.lock`). The installer creates that directory (mode 0700),
  never copies credentials into it, and says how to log Pi in there once. Gentle Dot stays the
  default; enabling the bridge is an explicit option and the two never run together on the same
  port. `host/README.md` and `docs/decisiones.md` describe it (a new decision entry).
- S11 (proof on the real machine): with the bridge running, `python -m jarvis.bench` (questions
  and the volume requests, `-n 3`) completes with every run done, and the TUI opens, shows a
  reply and a tool activity. Compare against the 2026-11-25 numbers in L4.
- S12 (L6, resilience findings of the T1 native review, lineage review-f249edc39d48167e; the
  reviewers' own claims, shortened only where marked):
  1. Slow client (`server.py:97-101`): "Hub.broadcast awaits each client's ws.send in sequence
     while holding one shared lock. A client that stops reading blocks the send once its write
     buffer fills [...]. During that time every other client and every Pi frame waits. [...] A
     fix would be a per-connection send queue or a send timeout that drops the slow client."
  2. Start order (`__main__.py:30-31`): "serve() starts the Pi child (bridge.start) before
     binding the WebSocket port, and both calls sit outside the try/finally that calls
     bridge.close(). When the bind fails with OSError, for example because Gentle Dot already
     holds port 4317 [...], the exception leaves serve() without an orderly bridge.close()."
     A failed bind must exit non-zero with a message naming the address and leave no Pi child.
  3. EOF (`pi.py:111-113`): "When Pi's stdout reaches EOF, read() awaits process.wait() with no
     bound before it returns None. If Pi closes or redirects stdout but keeps running, the
     reader hangs." Also: "The LimitOverrunError skip path [...] has no test."
  4. Stuck turn (`turns.py:88-93`): "A turn ends only on agent_settled, a prompt response, an
     abort, or Pi exiting. There is no timeout. If Pi stays alive but stops emitting [...]
     agent_state stays working and every later send is queued without bound." A turn with no Pi
     record for a configurable time (default 120 s; a pending question to the user does not
     count) ends with an `error` frame and Pi is restarted on the next `send`; the queue has a
     cap and a `send` beyond it gets an `error` frame.
  5. Token race (`token.py:63-65`): "In the concurrent-create branch, the other writer's file is
     read without an OSError guard [...]. A racing reader can see an empty file and raise
     'empty' TokenError." A token error is reported as a token error, never as a listen error.
  6. Origin (`server.py:143-145`): "Rejecting browser Origin headers would add defense in depth
     for an endpoint that fronts PC control." A connection that carries an `Origin` header is
     refused; Jarvis's clients send none.
- S13 (L7, findings of the T2 verify and native review, lineage review-fde2f100406dc30c):
  1. Stale questions (`turns.py:360`, `_finish` at `:306`). Verify: "the bridge doesn't notice
     when Pi closes a dialog by itself. [...] The bridge keeps the question pending, so clients
     get no `ask_resolved` and keep a stale `ask`. Worse, `_watch` then treats every later turn
     as 'waiting for the user'." When a turn ends, every question still pending is resolved
     for the clients (`ask_resolved`) and no longer pauses the stuck-turn watchdog; a late
     `ui_response` to it is not forwarded to Pi.
  2. Old reader after a timeout (`turns.py:368-381`). Verify: "when a stuck turn times out,
     the old Pi's reader is cancelled only after `_finish()` has already started the queued
     `send` and launched a new Pi. [...] the old Pi's late 'OLD' reply and `agent_settled`
     were attributed to the new turn and ended it. The new Pi's real reply 'NEW' was then
     dropped." No record of a Pi that was given up on may reach a later turn.
  3. Model after a restart (`turns.py:239`, `pi.py:67`). Verify: "the model chosen with
     `model_set` is lost on a Pi restart. [...] `ready.model` and the `auth_providers` entry
     still say `anthropic/claude-x`, but `models.current` shows the configured default". A
     restarted Pi runs the model and thinking level last chosen with `model_set`, and every
     frame that names the current model agrees with what Pi runs.
  4. Partial `model_set` (`turns.py:236-246`). Review: "set_model applies Pi's set_model and
     updates self._model before it checks the requested thinking level against the new
     model's levels. If that check fails, or set_thinking_level is refused, the sender gets a
     thinking_unavailable error. Pi and the ready frame still run the new model, and no models
     frame is broadcast". A `model_set` either applies whole and is broadcast, or leaves Pi on
     the previous model and thinking level and answers the sender with an `error`.
  5. Token file encoding (`token.py:35`, `:78`). Verify: "a token file that isn't valid UTF-8
     raises `UnicodeDecodeError`, which isn't caught. The result is a traceback and exit 1,
     the listen-error exit code, contrary to S12.5." It is a token error, exit 2, no traceback.
  6. Writer errors (`hub.py:89-94`). Review: "The per-connection writer only catches
     ConnectionClosed. Any other exception from ws.send ends the task silently while the peer
     stays registered." Such a client is logged and dropped at once.
  7. Shutdown and busy requests (`turns.py:268`, `server.py:189`). Verify: "A `models_list`
     waiting on a silent Pi blocks that client's `abort` for up to 10 s. `bridge.close()`
     doesn't cancel pending commands, so shutdown took 10.0 s". `abort` is never delayed by
     another request of the same client, and closing the bridge cancels pending commands.
- S14 (L8, left for a later pass; none shows up with one client and occasional model
  switches): (1) more than 8 model requests of one client waiting on a silent Pi block that
  client's read loop, so its `abort` waits and shutdown takes about 10 s per queued request
  (`server.py:198`, `__main__.py:51`): answer the extra request with an `error` instead; (2)
  model switches are not serialized across clients and a rollback can undo another client's
  successful switch (`turns.py:241-242`); a disconnect in the middle of a `model_set` can
  leave Pi half-switched (`server.py:204-206`); a failed put-back is only logged
  (`models.py:110`); a `models()` failure after a successful switch answers `error` with Pi
  already switched (`turns.py:244-247`); (3) a `model_set` followed at once by a `send` can
  start the turn on the previous model (`server.py:196-200`); (4) an unexpected exception in
  the model-request worker is only logged, the sender gets no answer (`server.py:216-217`);
  (5) the watchdog of a new turn starts before the new Pi has launched, so a launch slower
  than the record timeout reports `agent_timeout` yet still writes the prompt to Pi
  (`turns.py:313-316`); (6) `close()` does not stop a relaunch already running
  (`turns.py:294`).
- S15 (L9, findings of the T3 verify and native review, lineage review-d69a3d4d333ab087):
  1. Verify, blocker (`jarvis-pibridge.service.in:136`): "`ReadWritePaths=%h/.pi` exposes your
     whole real Pi config directory (`PI_CODING_AGENT_DIR` defaults to `~/.pi/agent`). That
     holds `settings.json`, `trust.json`, `extensions/`, `npm/node_modules` [...] and the saved
     logins. [...] From the agent's shell, it could plant a `.ts` file in
     `~/.pi/agent/extensions/` or edit `npm/node_modules`. Your interactive Pi runs outside the
     sandbox and would execute that code on its next start". Fixed by S10 as rewritten.
  2. Verify (`install.sh:877-888`): "If `enable` fails after the other unit has been disabled,
     nothing is running. The installer prints no message of its own saying so, or how to
     recover". It must say that neither service is running and how to recover.
  3. Review (`jarvis-pibridge.service.in:47-53`): "The unit relies on jarvis.pibridge reading
     the variables JARVIS_PI_HOST, JARVIS_PI_PORT, JARVIS_PI_EXECUTABLE, JARVIS_PI_PC_CONTROL
     and JARVIS_PI_AGENTS_MD. [...] No part of this candidate proves the bridge consumes these
     variables." A test proves every `JARVIS_PI_*` variable the unit sets is read by the
     bridge's option parser.
  4. Review (`install.sh:270`): "With --pi-bridge, the options --gentle-dot-dir,
     --allow-unpinned and --allow-dirty are accepted and then silently unused". They are
     refused together with `--pi-bridge`, like `--pi` without it (91c124d).
  5. Review (`install.sh:289`): "In bridge mode the installer runs node --version but does not
     enforce a minimum major version, unlike the Gentle Dot path." Same minimum as that path.
  6. Review (`install.sh:297`): "The refusal when ~/.pi exists but is not a directory has no
     test." Whatever directory check remains after S10 has a test.
  7. Docs. Review (`host/README.md:300-307`): "The README says that without --enable the
     bridge install 'no toca nada en systemd'. The installer [...] does run `systemctl --user
     daemon-reload` when the unit changes." Verify: `README.md:280` "una sola diferencia" is
     true for the sandbox only; `README.md:334` "every option works as `JARVIS_PI_<NOMBRE>`"
     is wrong for `--pi` (`JARVIS_PI_EXECUTABLE`) and `--extension` (`JARVIS_PI_EXTENSIONS`);
     `docs/decisiones.md:450`: D24 "never mentions D8. On a bridge machine the agent is no
     longer 'limited by Gentle Dot's guardrails' (D8's permission dialog, blocklist, rate
     limit); only the pc-control extension's own guards (D20/D23) remain." D24 must say so, and
     that the bridge's Pi keeps Pi's default tools (read, bash, edit, write) inside the sandbox.

## Tasks

- [x] T1 S2,S3,S4,S5 — Bridge core: server, auth, one turn with deltas, abort, Pi process
  lifecycle, tested against a fake Pi process and with the unchanged `GentleDotAgent`. Route:
  gentle-ai-worker. Commits 715494e (hello and token guard), 6295ce9 (turns on the Pi child)
  and the hardening fix after verify (L5). Host 291 passed, 6 skipped; backend 82 passed, 7
  skipped; pyright 0 errors on `host/jarvis/pibridge`. Independent verify: S2-S5 pass (L5).
- [x] T2 S6,S7,S8,S9,S12 — Activities, questions, models, several clients and history, tested
  with the unchanged `JarvisClient`, plus the resilience fixes of S12. Route: gentle-ai-worker,
  branch feat/pi-bridge-clients on feat/pi-bridge (PR #41), after T1. Code: 74d51ed (activities,
  several clients, history), f8c364a (questions, models), 3c92381 (S12). Host 319 passed, 6
  skipped. Fix pass pending: T5.
- [ ] T3 S10 — systemd unit, installer, README and decision entry. Route: gentle-ai-worker,
  branch feat/pi-bridge-install on feat/pi-bridge-clients (PR #42): 4f5e270 (unit, installer,
  tests), ceaa832 (docs), 91c124d (review correction: refuse `--pi` without `--pi-bridge`).
  Installer suite 241 passed. Native review approved after the correction. Reopened by L9.
- [ ] T7 S10,S15 — Give the bridge's Pi its own agent directory and fix the other T3
  findings. Route: gentle-ai-worker, same branch.
- [x] T5 S13 — Fix pass after the T2 verify and review. Route: gentle-ai-worker, same branch: bde49a8. Host 335 passed, 6 skipped; backend 82 passed, 7 skipped; pyright 0 errors. Native review approved (lineage review-03ea5a3391aa7fc2); independent verify: S13.1-S13.6 pass, S13.7 partial (L8).
- [ ] T6 S14 — Second hardening pass on model switching and backlog. Route: gentle-ai-worker, after the bridge is in use.
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
- L6 (2026-11-25, T1 delivery): pushed feat/pi-bridge and opened PR #41 (core, 1/3). Native
  review of the T1 range approved (risk, resilience, reliability; lineage
  review-f249edc39d48167e) with eight non-blocking findings; the ones worth fixing are S12 and
  go with T2. T2 continues on branch feat/pi-bridge-clients, stacked on PR #41.
- L7 (2026-11-25, evidence, T2). The unchanged `python -m jarvis.bench --port 4391
  --token-file <temp> --include-pc --only question --only volumen-sube --only volumen-baja
  --only spotify -n 3` ran through the bridge with the real `pi`: 24 of 24 runs done, 0 errors,
  1 model call per question and 2 per tool request; medians: questions 1.89-2.51 s, volume up
  4.76 s, volume down 4.18 s, Spotify 4.37 s (Gentle Dot the same day, L4: 6.59-7.41 s, 15.96
  s, 14.10 s, 13.80 s). No orphan Pi process. Native review of the T2 range approved (lineage
  review-fde2f100406dc30c, 3 non-blocking findings). Independent verify: S6, S8, S9 and S12.1-4
  and S12.6 pass; S7 and S12.5 partial; no blocker; security checks pass (nothing before a
  valid hello for the 7 new request types, no frame to an unauthenticated socket, `Origin`
  refused with 403). The findings worth fixing are S13 (T5).
- L8 (2026-11-25, evidence, T5). Real `pi` through the bridge after bde49a8 (unchanged bench):
  questions and volume n=2, 14 of 14 done (questions 1.88-2.52 s, volume 4.85 / 3.81 s);
  `--model openai/gpt-6.1-terra --thinking low` applied and `--list` showed it as current;
  back to `openai/gpt-6.1-sol` low, 1.62 s; after stopping the bridge no child process was
  left and the port was free. Correction to L5 and L7: the earlier "no orphan" checks searched
  the process list for `pi --mode rpc`, but Pi renames its process to `pi`, so they proved
  nothing; a bridge from an earlier run had in fact been left running by the test script (not
  by the bridge) and was stopped. Orphans are now checked through the bridge's child pids.
  Likely reason Gentle Dot is slower (seen in the process list, not measured in isolation):
  its daemon runs Pi with the `gentle-pi` package loaded, whose orchestrator prompt goes with
  every model call; the bridge starts Pi without it. Independent verify of bde49a8: the seven
  original failures are gone; two should-fix issues and two notes remain, recorded as S14.
  Pushed feat/pi-bridge-clients and opened the second PR (2/3), stacked on PR #41.
- L9 (2026-11-25, evidence and requirement change, T3). Native review of the T3 range (lineage
  review-d69a3d4d333ab087) required one correction, done in 91c124d and validated; it closed
  approved with six non-blocking findings. Independent verify: FAIL on security only — the
  installer mechanics pass (default install byte-identical over 13 steps, switching order
  correct, hardening lines identical, `systemd-analyze` clean), but write access to the whole
  `~/.pi` lets the sandboxed agent plant code the user's unsandboxed Pi runs. Decision
  (technical, taken to keep the sandbox's guarantee): S10 is rewritten so the bridge's Pi gets
  its own agent directory and `~/.pi` stays read-only. Cost for the user: one login of Pi in
  that directory. The remaining findings are S15 (T7). T3 stays open until T7 lands.
