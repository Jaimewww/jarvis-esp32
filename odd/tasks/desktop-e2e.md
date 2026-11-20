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

- [ ] T1 (S1-S3): explore options, build the runner and cases, document. Route: worker. Commit: —

## Log

- L1 (usuario, 2026-11-20): "tengo que salir dejare de probar, entrega todo y crea un mecanimos
  para que este tipo de pruebas se haga en automatico"
