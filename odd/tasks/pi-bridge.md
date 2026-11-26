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
  (`turns.py:294`). Installer (L12): (7) run with a `HOME` that is not the user's real
  home, the agent directory is written from the installer's `$HOME` while the other writable
  paths use systemd's `%h`, so it can land inside `~/Jarvis` or `~/.gentle-dot`
  (`install.sh:220`, `jarvis-pibridge.service.in:143`): render it with `%h` or check `$HOME`
  against the passwd home; (8) a symlink loop in the path passes the `realpath -m` comparison
  and fails later at `mkdir`, after the unit was written (`install.sh:330`); (9) the modes of
  the parents `~/.local`, `~/.local/state`, `~/.local/state/jarvis` are not checked; (10) the
  recovery hint after a failed `start` can leave both units enabled (`install.sh:944`); (11)
  the character check on the agent path was dropped with the option and now rests on the
  shared check of `~/Jarvis` (`install.sh:219-220`): add a test for a `HOME` with a space.
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
- S16 (L10, findings of the T7 verify and native review, lineage review-e031df68a5d3e6b2):
  1. Verify, blocker (`install.sh:331-334`): "the check only refuses paths *inside* `~/.pi`.
     `$HOME`, `/` or `~/.gentle-dot/..` make all of `~/.pi` (or everything) writable and cancel
     `ProtectHome`. Narrowest fix: also refuse when `"${user_pi_real}/"` starts with
     `"${pi_agent_real}/"`, and add tests for `$HOME`, `/` and a symlink to `$HOME`."
  2. Verify (inferred from systemd.exec(5), not run): "`gentle-dot.service` can also write
     `~/.gentle-dot`, and in its namespace `pi-agent` is not a mount point. Its agent could
     swap `pi-agent` for a symlink to `~/.pi`. systemd resolves symlinks in `ReadWritePaths`
     [...]. Fix: keep the agent directory outside both units' writable paths, for example
     `~/.local/state/jarvis/pi-agent`." The default agent directory moves outside every path
     either service can write, and the installer refuses an agent directory that is inside,
     or an ancestor of, any path the two units make writable (`~/.gentle-dot`, `~/Jarvis`).
  3. Verify: "the 'safe' login Pi can still run code the agent planted in the agent directory.
     `-ne` does not stop package resolution. Packages listed in the planted `settings.json` are
     installed at startup, and `npmCommand` is an arbitrary command [...]. `"!command"` keys
     in `auth.json` or `models.json` run when `/login` resolves providers [...]. `bin/fd` and
     `bin/rg` in the agent directory are preferred over the system ones [...]. The first login,
     done before any agent turn, is safe: the bridge has no credentials, so the agent cannot
     act. A re-login after use is not, yet the installer prints the command on every run and
     `host/README.md:309-310` presents the flags as the safeguard." The first login is done in
     a directory the agent has never written; the installer and the README give a re-login
     procedure that never runs Pi outside the sandbox in a directory the agent has written
     (stop the service, move the old directory aside without running Pi in it, create a fresh
     empty 0700 directory, log in, start), and the README no longer presents the flags as the
     safeguard.
  4. Verify, notes: `host/jarvis/pibridge/config.py:53-54` "the help says every option reads
     `JARVIS_PI_<NAME>`, which is wrong for `--pi` (`JARVIS_PI_EXECUTABLE`) and `--extension`
     (`JARVIS_PI_EXTENSIONS`)"; "A failing `disable --now` of the other unit (`install.sh:918`)
     exits without the S15.2 message"; "The mode of an existing agent directory is not
     checked" (an existing one that group or others can enter is refused).
- S17 (L11, replaces the configurable part of S16.1 and S16.2). Verify, blocker
  (`install.sh:345-355`, `:422`): the installer "checks overlap only on `realpath -m`, but
  [...] writes `${PI_AGENT_DIR}` as typed into both `PI_CODING_AGENT_DIR` and
  `ReadWritePaths`. A path that is literally inside `~/Jarvis` or `~/.gentle-dot`, or an
  `XDG_STATE_HOME` there, passes when a symlink component exists." Should-fix: "An option or
  `XDG_STATE_HOME` outside `$HOME` in a folder the user owns [...] has a parent both units can
  write. That allows the same symlink swap". Three verifies in a row found a new way through
  the configurable location, and nobody asked for it to be configurable, so it is removed:
  1. The agent directory is fixed: `$HOME/.local/state/jarvis/pi-agent`. There is no
     `--pi-agent-dir` option, no `JARVIS_PIBRIDGE_AGENT_DIR`, and `XDG_STATE_HOME` is not
     read. Passing `--pi-agent-dir` is an unknown option.
  2. The installer refuses to install the bridge when that path, resolved, differs from the
     path as written (a symlink in any component below `$HOME`), and the unit carries that
     same literal path in `PI_CODING_AGENT_DIR` and `ReadWritePaths`.
  3. Notes from the same verify: with `--enable` on an empty directory the message must not
     say the agent never wrote there (Pi has started in it; it is harmless because without a
     login no tool runs: say that instead); the neither-is-running message names the step
     that actually failed (`disable` of the other unit or `enable`/`start` of this one).

- S18 (L14, findings of the T6 verify and native review, lineage review-8aa3fff6edad51b8, on
  45fee37):
  1. Verify, blocker (`server.py:290`, `turns.py:250-256`): "if the bridge shuts down while a
     `model_set` is pending, it starts a new Pi after `close()` and never stops it. [...] The
     shield at `server.py:290` keeps the switch running after the connection is cancelled.
     `close()` fails its commands, and then `_settle` / `put_back` launch a new Pi through
     `_command`. The result is 2 launches, the second Pi not closed, and `bridge._process` set
     again after `close()` returned". Review R3-003: "Nothing tracks or awaits that task
     afterwards." After `close()` returns no Pi is launched or left running and no switch is
     still running, also in the real shutdown order of `__main__`.
  2. Verify, should-fix (`server.py:216` vs `:245`): "I sent `model_set`, `send` and `abort`
     back to back: the abort did nothing and the turn started anyway". Review R3-002: "At the
     baseline the send started at once and the abort stopped it." An `abort` cancels a `send`
     of the same client that still waits behind its `model_set`: that turn never runs and the
     client ends `idle`.
  3. Verify, should-fix: "a Pi that goes silent after `set_model` makes the failed switch take
     3 timeouts (30 s in real time): `models()`, then `put_back`, then `_settle`. Before this
     commit it took 10 s. `_switching` is held the whole time". A failed switch on a silent Pi
     is answered within one command timeout, as before 45fee37.
  4. Review R3-004 and R3-001 (`turns.py:348-352`): "Abort during a launch, which also reaches
     this branch, has no test"; "A Pi launch that never completes [...] agent_state stays
     'working'". An `abort` during a launch that has not finished ends the turn with `idle`
     and the next `send` works. No new timeout is added for the launch itself.
  5. Verify, note: "A `send` waiting behind a `model_set` counts toward the cap of 8, so it can
     get `busy` instead of `queue_full`." A `send` refused for a full queue gets `queue_full`,
     the code clients already know for it.

- S19 (L15, left for a later pass, none is a blocker): (1) after a `set_model` that Pi accepted
  and then answered nothing for one command timeout, `ready.model` and `auth_providers` name
  the previous model while a Pi that recovers runs the new one, until Pi is relaunched;
  `models_list` shows the truth (`turns.py:264-272`); (2) a launch that fails after an `abort`
  already ended its turn may emit an error for a finished turn or end a newer one; no test for
  abort-then-launch-failure or a second `send` during the aborted launch (`turns.py:170-172`);
  (3) the "no switch still running" assertion filters tasks by a name containing `switch` and
  may match nothing (`test_pibridge_hardening.py:364-366`); (4) `close()` sets no closed flag:
  a `model_set` that starts after it, when `bridge.close()` runs before `server.close()`,
  could launch Pi (`turns.py:340-345`); (5) an `abort` of the client that sent the `send` is
  read only after the launch ends, as at dcc68c6: up to two command timeouts on a silent new
  Pi; (6) in the `__main__` order a Pi relaunched during shutdown gets its prompt before it is
  closed.

- S20 (L16). User: "es que no tengo cuentas aparte de claude, en ese caso deberias intalar
  claudebridge o un deepseek por api, que renta mas"; chosen: "claude-bridge con carpeta propia
  (recomendado)". The Pi extension pi-claude-bridge (0.13.0) uses the Claude Agent SDK, which
  spawns Claude Code; Claude Code writes its config directory (sessions under `projects/`,
  `.claude.json`, token renewal) and honors `CLAUDE_CONFIG_DIR`.
  1. With `--pi-bridge` the unit gives Claude Code a directory of its own, fixed at
     `$HOME/.local/state/jarvis/claude-config`, passed as `CLAUDE_CONFIG_DIR`, the only Claude
     path the service can write. `~/.claude` and `~/.claude.json` stay read-only. No option or
     variable moves it (S17 lesson).
  2. The installer creates it 0700 and applies to it every refusal the Pi agent directory has
     (S16, S17, S14 items 7-11): symlink in a component, not a directory, group or other
     permissions on it, writable parents, wrong `HOME`, unit-unsafe characters. Nothing is
     written before the checks.
  3. Every sandbox layer of the unit allows writing there and nowhere new (`ReadWritePaths`,
     the Landlock layer of `jarvis-sandbox-exec`).
  4. The installer prints the one-time Claude login, before the first start of the service:
     `env CLAUDE_CONFIG_DIR=<dir> claude` and `/login`, with the same warning as for Pi: once
     the agent wrote in the directory, do not run `claude` in it; move it aside and log in on a
     new one.
  5. host/README.md explains the Claude subscription setup: the drop-in with
     `JARVIS_PI_MODEL=claude-bridge/claude-sonnet-5-5`, `JARVIS_PI_THINKING=low` and
     `JARVIS_PI_EXTENSIONS=%h/.pi/agent/npm/node_modules/pi-claude-bridge`, plus the login.
  Not requested: DeepSeek, a different default model in the repo (stays
  `openai/gpt-6.1-sol`), an installer option for the model.

- S21 (L17, left for a later pass, none is a blocker): (1) with `--enable` on an empty Claude
  Code directory the installer starts the service and then tells the user to stop it and run
  `claude` there; emptiness is not checked again, and a Pi already logged in could have
  written hooks or settings in between (`install.sh:1032-1036`): until fixed, install without
  `--enable`, log in, then enable; (2) the printed `claude` login command sets no working
  directory: run from `~/Jarvis` or `~/.gentle-dot` it could load project files the agent can
  write; prefix it with `cd ~ &&`; (3) `XDG_CONFIG_HOME` or `--bin-dir` pointing inside an own
  directory makes the installer create it 755 and non-empty before the 0700 creation
  (`install.sh:644,725` before `:945`); same at the parent commit for `pi-agent`; (4) no test
  for the upgrade state: Pi directory with a login, Claude Code directory new; (5) refusals on
  the shared parents name only the Pi directory.

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
- [x] T3 S10 — systemd unit, installer, README and decision entry. Route: gentle-ai-worker,
  branch feat/pi-bridge-install on feat/pi-bridge-clients (PR #42): 4f5e270 (unit, installer,
  tests), ceaa832 (docs), 91c124d (review correction: refuse `--pi` without `--pi-bridge`).
  Installer suite 241 passed. Native review approved after the correction. Reopened by L9.
- [x] T7 S10,S15 — Give the bridge's Pi its own agent directory and fix the other T3
  findings. Route: gentle-ai-worker, same branch: 4dc0e0f. Installer suite 264 passed; host
  346 passed, 6 skipped. Native review approved (lineage review-e031df68a5d3e6b2).
  Independent verify: FAIL on one option (S16.1); default install closed (L10).
- [x] T8 S16 — Close the agent-directory gaps found by the T7 verify. Route:
  gentle-ai-worker, same branch: 1a8d289. Installer suite 287 passed; host 347 passed, 6
  skipped. Native review approved (lineage review-5704c7e2a62b42c3). Independent verify:
  S16.3 and S16.4 pass, S16.2 fails through a symlinked component (L11).
- [x] T9 S17 — Make the agent directory fixed instead of configurable. Route:
  gentle-ai-worker, same branch: 9508d8b. Installer suite 277 passed; host 347 passed, 6
  skipped. Native review approved (lineage review-66a5cb63f31ffe4d). Independent verify:
  S17.1-S17.3 pass, no blocker (L12).
- [x] T5 S13 — Fix pass after the T2 verify and review. Route: gentle-ai-worker, same branch: bde49a8. Host 335 passed, 6 skipped; backend 82 passed, 7 skipped; pyright 0 errors. Native review approved (lineage review-03ea5a3391aa7fc2); independent verify: S13.1-S13.6 pass, S13.7 partial (L8).
- [x] T6 S14 items 1-6 — Second hardening pass of the bridge: model requests, model switching,
  watchdog and close. Route: gentle-ai-worker, branch feat/pi-bridge-hardening on main:
  45fee37. Host 356 passed, 6 skipped. Native review approved; verify found one blocker (L14),
  fixed by T11.
- [x] T10 S14 items 7-11 — Installer hardening: agent directory and `HOME`, symlink loop,
  parent modes, recovery hint, `HOME` with a space. Route: gentle-ai-worker, branch
  feat/pi-bridge-installer-hardening on feat/pi-bridge-hardening's first commit: 3ac41e2,
  README 7799533. Installer suite 284 passed. Review approved, verify pass (L14). PR #44.
- [x] T11 S18 — Fix pass after the T6 verify and review. Route: gentle-ai-worker (same worker
  continued), same branch: d164557. Host 362 passed, 6 skipped; pyright 0 errors. Native review
  approved (lineage review-bed6eb353178c6bd). Independent verify: S18.1-S18.5 pass, no blocker
  (L15). Leftovers: S19, no task yet.
- [x] T12 S20 — Claude Code config directory of the bridge. Route: gentle-ai-worker, branch
  feat/pi-bridge-claude on main: b5fdce1 (installer, unit, tests), b0f9ba6 (docs). Installer
  suite 301 passed; host 362 passed, 6 skipped. Native review approved (lineage
  review-1fda5d26b526cedf). Independent verify: S20.1-S20.5 pass, no blocker (L17).
  Leftovers: S21, no task yet.
- [x] T4 S11 — Run the bench and the TUI against the bridge on the user's machine; record the
  numbers. Route: inline with the user. Done with Claude by subscription (L18).
- [ ] T13 S19 — Bridge leftovers of the T11 verify: model state after a silent `set_model`,
  abort-then-launch-failure, the `switch` assertion, a closed flag in `close()`, abort read
  during a launch, shutdown order in `__main__`. Route: gentle-ai-worker, branch
  feat/pi-bridge-s19 on docs/pi-bridge-real-run (L19).
- [x] T14 S21 — Installer leftovers of the T12 verify: `--enable` on an empty Claude Code
  directory, `cd ~ &&` in the printed login, own directories created 755 before the 0700
  creation, upgrade-state test, refusals naming the right directory. Route: gentle-ai-worker,
  branch feat/pi-bridge-s21 on docs/pi-bridge-real-run (L19): 93a0cb0 (installer, tests,
  README), 93fcf45 (verify notes: a restart owed during the hold becomes a stop, update hints
  and stale link kept while held, Pi steps in the Claude-first order, README parent refusals).
  Independent verify: S21.1-S21.5 pass, four should-fix notes fixed by 93fcf45. Native review
  of 93a0cb0..b9dc0d0 approved with non-blocking findings, fixed by 3c0d36b (the held run stops
  the bridge only when it is running, changed or not; named --enable cases; one symlink
  refusal). Native review of b9dc0d0..37541d3 approved with three non-blocking findings, fixed
  by 6a63582 (while held the bridge is always disabled and stopped, so an activating or enabled
  one cannot come up; the message only when it was not off). Installer suite 328 passed.
- [ ] T15 — Whisper picks up a lot of noise (L18). Route: inline, after T13/T14 and the tests
  (L19).

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
- L10 (2026-11-25, evidence, T7). Independent verify of 4dc0e0f: in the default install the
  unit can no longer write `~/.pi` (writable paths: `~/.gentle-dot`, `~/Jarvis`, the agent
  directory, private tmp); the default install is identical to the previous installer over 17
  steps and 119 compared artifacts except the help text and the new refusals; switching never
  leaves both units enabled; S15.2-S15.7 pass. FAIL because `--pi-agent-dir` accepts an
  ancestor of `~/.pi` (S16.1), plus two residual exposures (S16.2, S16.3). Native review of
  the same commit approved with four non-blocking findings, two of them the same as S16.1 and
  S16.3. Nothing of T3 is pushed until T8 lands and verifies.
- L11 (2026-11-25, evidence and decision, T8). Independent verify of 1a8d289: 31 option
  values, 7 `XDG_STATE_HOME` values and 6 symlinked-`HOME` cases refused as required; the
  default unit writes only `~/.gentle-dot`, `~/Jarvis`, `~/.local/state/jarvis/pi-agent` and
  private tmp, and neither unit can write an ancestor of that directory; the default install
  is byte-identical to the previous installer over 8 steps; the first-login and re-login
  procedures behave as printed and never run Pi outside the sandbox in a directory the agent
  has written; with `--enable` on an empty directory nothing an agent decides can run before
  the login (no model, the bridge never sends Pi's RPC `bash`). FAIL on a symlinked component
  inside `~/Jarvis` or `~/.gentle-dot` in the option value. Decision: the option is the
  recurring hole and was never requested (the T7 handoff asked for it by mistake), so S17
  removes it. The F1 exposure (the unit writing `~/.pi`) is closed for the default install.
- L12 (2026-11-25, evidence, T9 and T3 delivery). Independent verify of 9508d8b: S17.1, S17.2
  and S17.3 pass. `--pi-agent-dir` is an unknown option; `JARVIS_PIBRIDGE_AGENT_DIR`,
  `XDG_STATE_HOME` and `PI_CODING_AGENT_DIR` in the installer's environment change nothing in
  the unit (12 cases); a symlink in any of the four components is refused before anything is
  written; every failed `systemctl` step is named (10 cases); the default install is identical
  to the previous installer over 12 steps; hardening lines match `gentle-dot.service.in`
  apart from the agent directory and `Conflicts=`; `systemd-analyze` clean. With `HOME` equal
  to the user's real home the verifier found no option, variable or pre-existing state that
  lets the unit write beyond `~/.gentle-dot`, `~/Jarvis`, `~/.local/state/jarvis/pi-agent` and
  private tmp; the other installer inputs only move read-only paths. Remaining, no blocker: a
  deliberately wrong `HOME` at install time and four notes, recorded in S14 items 7-11. Still
  not verified anywhere: the unit started under a real systemd, and a real Pi login. Pushed
  feat/pi-bridge-install and opened the third PR (3/3), stacked on PR #42.
- L13 (2026-11-26, user request and delivery). User: "si dale mergea, arma el plan, con lo que
  puedes avanzar tu y luego ya vamos con las pruebas". Merged PR #41, #42 and #43 into main
  (03abe56, ed3753a, dcc68c6; #42 and #43 retargeted to main first). Plan: S14 now, before the
  bridge is in use, split by surface into T6 (bridge, items 1-6) and T10 (installer, items
  7-11); then T4 with the user.
- L14 (2026-11-26, evidence, T6 and T10). T10: 3ac41e2 (installer suite 284 passed), README
  note 7799533; native review approved (lineage review-c833d9659d05e5fe, four advisories: owner
  of the parents and mode of HOME not checked, group-writable parents refused under umask 002,
  HOME compared as text, parent-mode cases only 777); independent verify: S14.7-S14.11 pass,
  no blocker, Gentle Dot path unchanged over nine steps. T6: 45fee37 (host 356 passed, 6
  skipped; pyright 0 errors); native review approved (lineage review-8aa3fff6edad51b8, four
  advisories); independent verify: S14.1, S14.3, S14.4, S14.5 pass, S14.2 and S14.6 partial with
  one blocker (a Pi launched after `close()`), no deadlock found. Findings recorded as S18,
  task T11. Still not verified anywhere: a real `pi` subprocess on the new paths, real systemd.
- L15 (2026-11-26, evidence, T11). d164557: S18.1, S18.2, S18.3 and S18.5 fixed; for S18.4 the
  worker reported the same-client abort as not fixable without changing an existing test.
  Independent verify: S18.1-S18.5 pass, no blocker, no should-fix; close takes 0.001-0.002 s
  in six shutdown runs with no Pi launched after it; the same-client abort behaves the same at
  dcc68c6, so it is a pre-existing limit. Native review: the first reviewer run stopped on the
  relay's 120 s stream-idle bound with no verdict; the reoffered slots were captured once after
  the verify ended and the review closed approved with six advisories, recorded with the
  verify notes as S19. Pushed feat/pi-bridge-hardening and opened its PR. Still not verified
  anywhere: a real `pi` subprocess on these paths, and real systemd.
- L16 (2026-11-26, delivery, evidence and user decision). Merged PR #45 (9e7b1f8) and PR #44
  (8b8db5f) into main. T4 started on the user's machine: installed and enabled from main,
  the unit started under real systemd and listened; the first real turn failed with "OAuth
  refresh failed for openai [...] invalid_grant" (the user had copied the login file of their
  own Pi; the copy was moved aside and the directory recreated empty). User: "es que no tengo
  cuentas aparte de claude, en ese caso deberias intalar claudebridge o un deepseek por api,
  que renta mas"; answered the question "claude-bridge con carpeta propia (recomendado)". Read
  of pi-claude-bridge 0.13.0: `@anthropic-ai/claude-agent-sdk` 0.3.321 with its linux-x64
  binary package installed beside it, `CLAUDE_CONFIG_DIR` read at `src/index.ts:527,542`.
  Spike figures of 2026-11-25 for claude-bridge/claude-sonnet-5-5 low, outside the sandbox:
  questions 3.06-3.36 s, volume 3.89/3.95 s. Recorded as S20, task T12.
- L17 (2026-11-26, evidence, T12). b5fdce1 and b0f9ba6: one shared `check_own_dir` for both
  own directories. Native review approved with two advisories. Independent verify: 29 refusal
  cases each leave the HOME tree identical with no unit written and no `pi-agent` created;
  pre-set `CLAUDE_CONFIG_DIR`, `XDG_*` and the old agent-directory variables change nothing in
  the unit; Gentle Dot install identical to 41f4a96 over 11 steps; no blocker, two should-fix
  and notes recorded as S21. Still open for the real run: whether Claude Code writes outside
  `CLAUDE_CONFIG_DIR`, whether the SDK's bundled binary starts under the sandbox, whether the
  Pi of the bridge needs a login of its own for `claude-bridge/` models.
- L18 (2026-11-26, evidence and user report, T4). PR #46 merged (6920823). The user logged
  Claude Code into `~/.local/state/jarvis/claude-config`; the override sets
  `JARVIS_PI_MODEL=claude-bridge/claude-sonnet-5-5`, `JARVIS_PI_THINKING=low` and
  `JARVIS_PI_EXTENSIONS=%h/.pi/agent/npm/node_modules/pi-claude-bridge`. Under real systemd
  the first turn reached Anthropic (session accepted, the SDK binary starts under the
  sandbox, the bridge's Pi needs no login of its own for `claude-bridge/` models) and failed
  only on the subscription's 5-hour limit ("Claude quota exceeded — rate limit (five_hour)");
  the bridge shares that limit with the user's own Claude use. Claude Code wrote only inside
  `claude-config`; `~/.claude` and `~/.claude.json` unchanged. Bench after the reset
  (`--include-pc --only question --only volumen-sube --only volumen-baja --only spotify -n 2`,
  JSON `/tmp/jarvis-pi-spike/bridge-claude.json`): 16 runs, 0 errors, 15 done; questions
  1.29-1.60 s median (one model call), volume 2.70/2.72 s, Spotify 3.68 s median / 4.78 s
  worst (OpenAI through the bridge on 2026-11-25: 1.89-2.51 s, 4.18-4.76 s, 4.37 s). The one
  not-done run: `pc_open_app("spotify")` was refused (no Spotify desktop app in the app list)
  and the agent opened `open.spotify.com` instead. User, after trying the TUI: "funciona
  rapido, pero el whisper esta agarrando mucho ruido, pero bueno por ahora quedamos aca,
  anota y continuamos leugo". Open: Whisper picks up a lot of noise (not investigated yet);
  the old OpenAI copy `~/.local/state/jarvis/pi-agent.old-20261126-105432` is still there
  (the safety policy blocked deleting it; the user removes it by hand).
- L19 (2026-11-26, user): "primeor dale en lo que puedes avanzar automatico, luego pruebas y
  lo de whisper al final". The user removed `pi-agent.old-20261126-105432` ("listo ya
  borre"). Plan: T13 (S19) and T14 (S21) now as two parallel writers, then the tests, then
  T15 (Whisper noise).
