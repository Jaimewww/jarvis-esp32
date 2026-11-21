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

## Tasks

- [x] T1 (S1-S3): explore options, build the runner and cases, document. Route: worker. Commit: f021ec8
  (runner `host/e2e/run.sh` = `python3 -m jarvis_e2e`; on top of cherry-picked ad4cf60 as 4e315c8).
  Open: the VS Code `hola mundo` case FAILS against the current helper (L2).
- [x] T2 (S1, L2): fix the helper so the runner passes: plan wtype keycode slots (special keysyms on their real keycodes, text characters never on keycodes Chromium treats as special). Route: worker. Commit: ad9d9bc (runner cases 79eaabc). Text is now typed as `-k <keysym>`, not after `--` (L3).

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
