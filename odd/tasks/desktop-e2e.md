# Desktop E2E for PC control

Automate the manual wtype/VS Code tests of odd/tasks/s2-round2.md (L19-L27) so typing, keys,
focus and screenshots are checked by a script instead of the user. Branch `feat/desktop-e2e`.

## Specs

- S1 (L1): "crea un mecanismo para que este tipo de pruebas se haga en automatico". "Este tipo de
  pruebas" = the manual checks the user ran in s2-round2 L19-L27:
  - "escribí hola mundo en VS Code" while focused elsewhere must type "hola mundo" complete
    (bugs seen: "la mundo"; "Hola mundo, este es un texto de prueba." typed as
    "Hola mundo, ee es uno de prueba.").
  - Clearing a document in VS Code with the Vim extension: `Escape g g d shift+g` must leave it
    empty (bugs seen: "ggg", "ggdg").
  - Focus goes back to the window the user was in.
- S2: the test drives the real helper code path (the exact wtype argv that
  `host/input/jarvis_input` builds for `type`/`key`), real wtype, and a real Chromium/Electron
  client, and reads the resulting text back without a human. It must not type into the user's
  live session windows.
- S3: one command runs it and prints pass/fail per case; documented in host/input/README.md. It
  skips with a clear reason (exit 0 plus "SKIP: …", or a distinct code) when the machine lacks what
  it needs, so it never fails CI by accident.
- S4 (L5-L7): "se comio la C". In production the agent called `pc_key` `{"keys":"a",
  "window_id":7}` (intent "Entrar en modo inserción al final del texto en VS Code sin borrar
  contenido.", which needed a TUI confirmation answered with `y`), then `pc_type`
  `{"text":" Cada nuevo proyecto es una oportunidad…","window_id":7}`. VS Code+Vim showed
  "ada nuevo proyecto…": the leading space and the "C" were eaten. After a mode key and a text
  sent back to back, the text must land complete, including when the key needed a confirmation;
  a runner case reproduces the production sequence and fails before the fix.

## Tasks

- [x] T1 (S1-S3): explore options, build the runner and cases, document. Route: worker. Commit: f021ec8
  (runner `host/e2e/run.sh` = `python3 -m jarvis_e2e`; on top of cherry-picked ad4cf60 as 4e315c8).
  Open: the VS Code `hola mundo` case FAILS against the current helper (L2).
- [x] T2 (S1, L2): fix the helper so the runner passes: plan wtype keycode slots (special keysyms on their real keycodes, text characters never on keycodes Chromium treats as special). Route: worker. Commit: ad9d9bc (runner cases 79eaabc). Text is now typed as `-k <keysym>`, not after `--` (L3).

- [x] T3 (S4, L5-L9): reproduce "se comio la C" in the runner (key that needs a confirmation
  answered like the TUI does, then text, back to back). Route: inline (writer refused). Outcome:
  not reproduced; both production sequences added as regression cases, no helper change (L8-L9).

## Log

- L1 (usuario, 2026-11-20): "tengo que salir dejare de probar, entrega todo y crea un mecanimos
  para que este tipo de pruebas se haga en automatico"
- L2 (worker, 2026-11-20): nested niri (no headless backend in niri 26.04; no sudo, so no
  sway/cage), real helper in-process, real wtype, Brave + VS Code 1.147 with vscodevim 1.32.4,
  read back over CDP; ~17 s per run, teardown verified (no leftover processes). Five runs on
  f021ec8: browser `hola mundo`, browser L27 sentence + `’ — …`, browser `Escape g g d shift+g`
  (keydowns Escape/code Escape, g, g, d, G), both VS Code clears and the VS Code sentence PASS
  every time; **VS Code `i` + `hola mundo` FAILS every time** (`holamundo   `, `hola    mundo`,
  `h   olamundo`). Keydown log: `a/Backspace  /Tab`: the text keys land on the keycodes of
  Backspace and Tab, and VS Code dispatches shortcuts by `code`, so the space acts as Tab. Cause:
  wtype matches `-p` (keysym) entries and text (character) entries separately, so
  `register_chars` does not pre-build the text keymap; it adds N+1 extra entries and shifts every
  text key to keycode N+2…, here onto Backspace (14) and Tab (15). Before ad4cf60 the browser
  sentence also failed 4/4 (`Hola mundo este…`, comma on another special keycode). Not fixed
  here (not trivial): typing must keep text keysyms off keycodes VS Code binds (Escape, BackSpace,
  Tab, Return, modifiers, arrows…).
- L2 (runner, 2026-11-20): 6/7 pass; `hola mundo` in VS Code+Vim fails: register_chars puts characters on the keycodes VS Code reads as BackSpace/Tab (space acts as Tab). Likely also the cause of s2-round2 L27.
- L3 (worker, 2026-11-20): wtype 0.4 main.c: one keymap built while parsing argv; each new keysym
  is the next entry in order of first use, entry i = evdev i+1 (xkb i+9); `-k`/`-p`/`-P` share
  the entries and match by keysym, text matches by character only (never reuses a `-p` entry,
  always appended after all options); `-M`/`-m` take no keycode. So text after `--` is one
  contiguous block after the options and cannot avoid special keycodes once it has > 12
  distinct characters: text is now `-k 0x…` per character. `jarvis_input/slots.py` releases one
  keysym per keycode 1..max (specials on their real US keycode, shortcut letters then the rest
  on their own US key or the next free character key, private-use fillers elsewhere), then
  `-k Shift_L -s 250` (Shift_L on 42), then the keys/text; > 66 distinct characters = several
  wtype runs. Runner before (fea3934 + new checks): 2/9 pass (`hola mundo` and the sentence
  fail on Backspace/Tab/Enter/ControlLeft/ShiftLeft codes in Brave and VS Code; 108 distinct
  characters lose most text). After (ad9d9bc): 9/9 pass, three runs in a row (~20 s each).
  Runner fix: the nested niri now runs with `vblank_mode=0`; with the user's monitors off it got
  one frame callback per second and handled one key per second (`hhoollaa`, timeouts).
- L4 (worker, 2026-11-20): merged into `fix/pc-control-typing-e2e` with the T25/T26 chain
  (s2-round2 L30): `slots.py` replaced the chain's `register_chars`; new case `browser:
  screenshot with window_id` (T26: target page painted magenta, ≥ 5% of the decoded PNG,
  `window_id` echoed, focus restored; skipped without grim). CI job `host-input` runs
  `host/e2e/run.sh` and accepts exit 0 or 77 (SKIP on runners without Wayland). Runner on the
  integrated branch: 10/10 PASS in two runs in a row (~24 s), no leftover processes.
- L5 (usuario, 2026-11-21): "se comio la C" (screenshot: VS Code line 2 shows "ada nuevo proyecto
  es una oportunidad para aprender, transfo…", status INSERT, Ln 2, Col 1; Jarvis TUI shows the
  run list/open/list/screenshot/key/type/screenshot). Then: "si encaralo A, y lo de borrar si lo
  ejecuta bien" (A = give the key path the same settle as typing and wait longer before
  restoring focus; clearing with `Escape g g d shift+g` works).
- L6 (parent, 2026-11-21): jarvis-input journal 12:04: `key: a; intent 76 caracteres` →
  `confirmación … pedida a 1 suscriptores` → `permitida` → `enfocando 7` → `foco devuelto a 6` →
  `type: 230 caracteres, empieza ' Ca'` → `enfocando 7` → `foco devuelto a 6`. The key already
  has the 400 ms focus settle and the `-k Shift_L -s 250` prelude; focus is restored 0.1 s after
  wtype. Hypothesis 1: Vim was still NORMAL when the text came (' ' moves right, 'C' changes to
  end of line and enters insert).
- L7 (runner, 2026-11-21): new case `vscode+vim: key 'a' then type ' Cada nuevo proyecto', back to
  back` (no confirmation) PASSES: 12/12, so the 0.1 s restore alone does not reproduce it. The
  production key differed by the TUI confirmation (`y` in host/jarvis/controlview.py). Hypothesis
  2: the user's `y` keystroke reaches VS Code when the helper focuses it right after the answer
  (Vim `y` = yank operator pending; then `a` + ' ' is an invalid text object that cancels, and
  `C` enters insert). The clear case works because it starts with Escape. Not yet proven.
- L8 (runner, 2026-11-21): case `vscode+vim: key 'a' confirmed with y, then type ' Cada nuevo
  proyecto'` (control-socket subscriber answers while one `wtype -P y -s 100 -p y` holds `y` in
  the user's window) PASSES: 14/14 twice. Hypothesis 2 not reproduced. Journal timing: the
  production key and text were 6 s apart (12:06:01 restore, 12:06:07 type), not back to back.
  The agent's own screenshots: before the key, line 2 held "Hola mundo, ee es uno de prueba."
  (NORMAL, Ln 2 Col 35); after the text, line 2 shows "ada nuevo proyecto…" at the left edge with
  a horizontal scrollbar whose thumb (~21% of the track) fits a ~265-character line, i.e. the old
  text is probably still there, scrolled out of view. Whether " C" was eaten or only scrolled out
  cannot be settled: the buffer was closed unsaved and VS Code kept no backup.
- L9 (usuario, 2026-11-21): "ya escribio bien" (live retry 13:04: `key: i`, confirmed, then
  `type: 305 caracteres, empieza 'Cad'`, text complete). Decision (parent): no timing change
  without a failing case; keep both regression cases. If it happens again, keep VS Code open.
