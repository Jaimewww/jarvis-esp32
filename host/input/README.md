# jarvis-input — the only process that touches the screen (S13)

`jarvis-input` is a small Python 3 service (standard library only, `asyncio`). It is the
**only** process that runs `wtype`, `grim`, `niri msg` and `xdg-mime`, and that launches apps
(S15: it execs the checked `Exec` itself; no `gtk-launch`, no generic `xdg-open`). The
Gentle Dot daemon is sandboxed away from Wayland and asks this helper over a Unix socket; the
Jarvis TUI and `jarvis-stop` grant, revoke and confirm over a second socket the daemon cannot
reach. It replaces the S12 permission file, which the agent could forge.

* `jarvis_input/checks.py` — pure checks: key parsing (ported from
  `host/gentle-dot/extensions/pc-control-core.ts`), text limits, lookalike letters, blocklist,
  the rule table of actions that ask first (`confirm_reasons`, S10a), `open` validation, rate
  limiter.
* `jarvis_input/launch.py` — `open` (S15): finds the desktop entry, parses `Exec`, and refuses
  anything the sandboxed daemon could rewrite. Reads files only.
* `jarvis_input/server.py` — the two sockets, the in-memory permission, confirmations, and the
  child processes (always an `argv`, never a shell).
* `jarvis_input/__main__.py` — `python3 -m jarvis_input`.
* `tests/` — unittest suites (also run under pytest) with fake tools on `PATH`.

## Running

Installed by `host/systemd/install.sh` into `~/.local/share/jarvis/input/` and run by
`jarvis-input.service` inside `graphical-session.target`:

```sh
cd ~/.local/share/jarvis/input && python3 -m jarvis_input
```

It refuses to start (exit code 2) when `XDG_RUNTIME_DIR` is unset, relative, missing or not
owned by the user; when `jarvis/` or `jarvis-control/` is a symlink, not a directory or owned
by someone else; when a socket path holds anything that is not a socket; or when another
helper is already listening. A stale socket (nobody listening) is removed. Directories are
`0700` (fixed if looser), sockets `0600`, and connections from another uid (`SO_PEERCRED`) are
dropped. SIGTERM/SIGINT revoke, kill children and remove the sockets.

Logs go to stderr (the journal): `JARVIS_INPUT_LOG=DEBUG|INFO|WARNING`. Typed text and intents
are logged only as a length and at most the first 3 characters; screenshots only as a byte count.

## Protocol

JSON, one object per line (UTF-8, `\n`-terminated), one request → one response, any number of
requests per connection. Requests over **64 KiB** get an error and the connection is closed.
A connection idle for 30 s is closed (except subscribers). Errors are always
`{"ok": false, "error": "<Spanish message for the agent/user>"}`; invalid JSON, a non-object, a
missing `op` or an unknown `op` get one and the connection stays usable. The ops of one socket
are unknown on the other.

### `$XDG_RUNTIME_DIR/jarvis/agent.sock` (the agent extension)

All agent requests share a rate limit of **10 per second** (sliding window).

| Request | Permission | Response |
| --- | --- | --- |
| `{"op":"open","app":"<desktop-id>"}` | no (stage a) | `{"ok":true}` |
| `{"op":"open","url":"https://…"}` | no (stage a) | `{"ok":true}` |
| `{"op":"type","text":"…","intent":"…"}` | yes | `{"ok":true}` |
| `{"op":"type","text":"…","window_id":11}` (S16) | yes | `{"ok":true,"window_id":11}` |
| `{"op":"key","keys":"ctrl+l","intent":"…"}` | yes | `{"ok":true}` |
| `{"op":"key","keys":"ctrl+l","window_id":11}` (S16) | yes | `{"ok":true,"window_id":11}` |
| `{"op":"screenshot","scale":0.5}` | yes | `{"ok":true,"png_base64":"…"}` |
| `{"op":"screenshot","window_id":11}` (S17) | yes | `{"ok":true,"png_base64":"…","window_id":11}` |
| `{"op":"apps","query":"notas"}` (S14) | no | `{"ok":true,"apps":[{"id":"<desktop-id>","name":"…"}]}` |
| `{"op":"windows"}` (S14) | yes | `{"ok":true,"windows":[{"id":11,"app_id":"…","title":"…","focused":false}]}` |
| `{"op":"focus","window_id":11}` (S14) | yes | `{"ok":true}` |
| `{"op":"media","action":"volume_up"}` (T15) | no | `{"ok":true}` |

* `open` (S15; see [`open` hardening](#open-hardening-s15)): exactly one of `app` or `url`.
  `app` matches `[A-Za-z0-9][A-Za-z0-9._-]{0,254}`; `url` must be `http://` or `https://` with a
  host, ≤ 2048 chars, no whitespace or control/format characters, and is opened with the default
  browser from `xdg-mime query default x-scheme-handler/https` (no default, a failing query, or a
  browser entry that fails the checks = error). The checked argv is exec'd directly in a new
  session (`setsid`), with stdio on `/dev/null`, cwd `/` (or the entry's `Path`), `PATH` fixed to
  `/usr/local/bin:/usr/bin` and no `LD_PRELOAD`/`LD_LIBRARY_PATH`/`LD_AUDIT`; a non-zero exit
  within 5 s is an error, a process still running after 5 s counts as launched (and is reaped
  in the background).
* `type`: `text` 1–2000 characters, newlines and tabs allowed, other control characters not;
  `intent` optional, ≤ 500 characters. Runs `wtype <slots> -k Shift_L -s 250 -d 8 -k 0x… …`:
  each character as `-k <keysym>` (keysyms from libxkbcommon's `xkb_utf32_to_keysym` like wtype,
  so ’ — … € get their legacy keysyms; `\n` is Return, `\t` Tab; without libxkbcommon, the
  Latin-1 or `0x1000000`+code point keysym); see
  [wtype keycode slots](#wtype-keycode-slots). Text with more distinct characters than there are
  character keycodes (66) is typed in several wtype runs, each with its own slots and prelude,
  with the focus re-checked before each.
* `key`: up to 20 space-separated combos `mod+mod+key`; modifiers `ctrl|control`, `shift`,
  `alt`, `altgr`, `super|logo|win|meta|mod`; aliases `enter|return`, `esc`, `tab`, `space`,
  `backspace`, `delete|del`, `insert`, `home`, `end`, `pageup`, `pagedown`, arrows, `f1`–`f24`;
  otherwise an xkb keysym name, except raw keysym numbers (`0xff0d`, `U000D`), which are refused
  so every way to press Return/KP_Enter is seen by the confirmation rule (names match
  case-insensitively, as in wtype). Without libxkbcommon only a built-in table resolves
  (`slots.FALLBACK_NAMES`: letters, digits, ASCII punctuation names, the aliases, F1–F24,
  modifiers and common special keys); other names are refused. Runs `wtype <slots> -k Shift_L -s 250 -M mod … -k key … -m mod …`.
  A plain `shift+letter` is sent as the capital's keysym (`shift+g` → `-k 0x47`): wtype resolves
  `-k` names case-insensitively, so `-k G` or `-M shift -k g` would type `g`. A bare `Escape`
  followed by more keys is followed by a 300 ms pause (VS Code Vim leaving insert mode, L25).

### wtype keycode slots

wtype 0.4 (`main.c`) parses all its argv before sending anything and uploads one keymap. Each new
keysym gets the next entry in order of first use, and entry *i* is evdev keycode *i*+1 (xkb
*i*+9). `-k`, `-p` and `-P` share those entries and match them by keysym; text (after `--` or a
bare argument) matches by character only, so it never reuses a `-p` entry and always lands after
them. `-M`/`-m` only send modifier state and take no keycode. Chromium/Electron derive
`KeyboardEvent.code` from the keycode and VS Code dispatches shortcuts on it, so a space on
keycode 15 acted as Tab and an `a` on 14 as Backspace (desktop-e2e L2).

`jarvis_input/slots.py` therefore starts every `type` and `key` argv with one `-p <keysym>`
release (apps act on presses) per keycode from 1 up to the highest keycode used:

* special keys on their real US keycode: Escape 1, BackSpace 14, Tab 15, Return 28, the
  modifiers (Control/Shift/Alt/Super L and R, AltGr), Home/End/Prior/Next/arrows/Insert/Delete,
  F1–F24, KP_Enter, Menu, Print, Pause, Caps/Num/Scroll_Lock;
* keys pressed with a modifier (`ctrl+l`), then every other keysym in first-use order, on their
  own US key when free (`l` on KeyL, `,` on Comma, `H` on KeyH); the rest (`’ — …`, a second
  case of a letter) on the next free character key: the main block, Space, IntlBackslash, then
  the numpad (never NumpadEnter);
* every other keycode up to the highest one gets an inert private-use filler (`U+E000`+keycode).

Then the prelude `-k Shift_L -s 250` (Shift_L on its own keycode 42; the tap carries the keymap
and the sleep lets Chromium/Electron apply it, else the first keys are dropped, L19/L25) and
everything else as `-k`: key names, or `0x…` keysyms for text, so argv has no raw text and no
`--`. argv is bounded: ≤ 2 × 194 slot arguments + 2 per character (≤ 4.4k arguments for 2000).
* `screenshot`: optional `scale` in `[0.05, 1]`; runs `grim [-s <scale>] -`.
* `screenshot` with `window_id` (S17), under the action lock: the target gets the `focus`
  checks with the screenshot rules (listed, not blocklisted; terminals allowed); the focused
  window is remembered; if the target is not focused it is focused, confirmed (≤ 500 ms) and
  given 400 ms to settle; `niri msg -j focused-output` names its monitor; focus is re-checked
  against the target; then `grim [-s <scale>] -o <output> -` (every output if the name is
  unreadable or not a connector name), and focus goes back as for `type`/`key`. The capture is
  the target's whole monitor, not just the window: `niri msg action screenshot-window` also
  overwrites the user's clipboard, and niri does not expose tiled-window geometry for
  `grim -g`. The response echoes `window_id`. Without `window_id` nothing changes.
* `apps`: **reads files only, never runs anything.** Data dirs by precedence:
  `$XDG_DATA_HOME` (or `~/.local/share`), `$XDG_DATA_DIRS` (or `/usr/local/share:/usr/share`;
  relative entries ignored), then `<data home>/flatpak/exports/share` and
  `/var/lib/flatpak/exports/share` when present and not already listed. Each `applications/`
  tree (depth ≤ 4, no directory symlinks, ≤ 5000 files, files ≤ 256 KiB, regular files only,
  UTF-8) gives desktop-ids = relative path with `/` → `-`, minus `.desktop` (ids that `open`
  would reject are skipped). Only the `[Desktop Entry]` group counts. The first dir with an id
  wins, even if that entry is hidden (it masks lower ones). Skipped: `Type` ≠ `Application`,
  `NoDisplay=true`, `Hidden=true`, no name. `name` = `Name[es]`, else `Name` (≤ 200 chars).
  Sorted by name (accent/case-insensitive), at most **500**; `"truncated":true` is added when
  capped. `query` (optional, ≤ 200 chars): accent/case-insensitive substring over both names,
  `GenericName`, `Keywords` (default and `[es]`) and the id.
* `windows`: runs `niri msg -j windows`; `title` truncated to 200 chars; at most 500 windows.
  Non-zero exit, timeout or unexpected JSON = error.
* `focus`: `window_id` an integer ≥ 0 that must appear in `niri msg -j windows`; refused when
  the window is on the blocklist or is a terminal (the same rules as `type`, below); then runs
  `niri msg action focus-window --id <id>` and polls `focused-window` for up to 500 ms: ok only
  once that window is focused, otherwise an error.
* `type`/`key` with `window_id` (S16), all under the action lock: the target gets the `focus`
  checks (listed, not blocklisted, not a terminal); confirmations (S10a, step 5 below) name
  and are judged on the **target** window and are asked before anything is focused (so the user
  can answer in the TUI); then the currently focused window id is remembered; if the target is
  not already focused it is focused, confirmed (≤ 500 ms) and given 400 ms to settle (Electron
  apps such as VS Code drop keys sent right after they regain focus), focus is re-checked against the target right before `wtype`,
  `wtype` runs, and finally (also when a later step failed) focus goes back to the remembered
  window if it still exists and is not the target — best effort, after a 100 ms pause; failures
  are only logged. The response echoes `window_id`. Without `window_id` nothing changes. Agent
  flow for typing: `apps` + `open` (or `windows`), then `type`/`key` with `window_id`; `focus`
  only to bring a window forward for the user.
* `windows`, `focus`, `type`, `key` and `screenshot` run one at a time (an action lock).
* `media` (T15): `action` is one of a closed set, each with a fixed argv (no caller text reaches it):
  `play_pause`/`next`/`previous` → `playerctl play-pause|next|previous` (the active player);
  `volume_up` → `wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+` (capped at 100%),
  `volume_down` → `wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-`, `mute`/`unmute` → `wpctl set-mute
  @DEFAULT_AUDIO_SINK@ 1|0` (explicit, never a toggle). It never sends keys, so it works with any window focused (or none)
  — unlike `key` with `XF86Audio*`, which goes to the focused window and is refused in terminals
  (odd/tasks/latencia.md L8). **No permission and no confirmation:** these are catalog actions,
  low risk by D8 (like `open`), and must work while `/control` is off; the shared rate limit
  still applies, no action lock (it does not touch focus or input), and each run is logged as
  `media <action>`. Errors: an unknown action (lists the valid ones); `playerctl`/`wpctl` not on
  the helper's `PATH` ("Falta … Pídele al usuario que lo instale"); `playerctl` exiting non-zero
  ("No hay ningún reproductor activo…", e.g. no player running); `wpctl` exiting non-zero.
  Timeout 3 s.

### `$XDG_RUNTIME_DIR/jarvis-control/control.sock` (TUI and `jarvis-stop` only)

| Request | Response |
| --- | --- |
| `{"op":"grant","minutes":30}` | `{"ok":true,"expires_at":"2026-11-20T15:30:00Z"}` |
| `{"op":"revoke"}` | `{"ok":true}` |
| `{"op":"status"}` | `{"ok":true,"enabled":true,"expires_at":"…Z"}` (`false`, `null` when off) |
| `{"op":"subscribe"}` | `{"ok":true}`, then a `state` event, then events |
| `{"op":"answer","id":"…","allow":true}` | `{"ok":true}`, or an error if unknown/already answered |

* `grant`: `minutes` defaults to 30, must be a positive number, and is **capped at 30**.
  A new grant replaces the old one. The permission is checked on every agent request and a
  timer also expires it on time.
* `revoke` (and expiry): clears the permission, **kills running `wtype`/`grim` children**
  (that request answers with an error) and denies every pending confirmation. The permission is
  also re-checked right before every `wtype` run (each run of a long text) and before `grim`, so
  a revoke during the focus settle or between runs stops the action with the "apagado" error;
  focus still goes back to the user's window (best effort: a failure there is only logged).
* Events (to every subscriber):
  `{"event":"state","enabled":…,"expires_at":…}` on subscribe, grant, revoke and expiry;
  `{"event":"confirm","id":"…","title":"…","message":"…","timeout_s":30}`.
* `answer` may come on the subscribe connection or on any other control connection. The
  first answer wins; later ones get an error. Responses to `answer` on a subscribe connection
  are interleaved with events: tell them apart by `ok` vs `event`.

## `open` hardening (S15)

`open` needs no permission, so it must not run code the sandboxed daemon could have written
(`~/.local/bin`, AppImages, `~/.local/share/applications`, `mimeapps.list`, …). For the app id
(or the default browser's id), using the **helper's** environment, never the caller's:

1. The entry is found with the same XDG precedence as `apps` (the first data dir wins). Not
   found = error.
2. Refused when the `.desktop` file (as found or after `realpath`) is under `$HOME`,
   `$XDG_RUNTIME_DIR`, `/tmp`, `/var/tmp` or `/dev/shm`, or this user can write it or its folder.
3. Refused when `Type` ≠ `Application`, `Hidden`/`NoDisplay=true`, `Terminal=true`, or the id,
   `StartupWMClass`, program or a non-option argument is a terminal (the same list as `type`).
4. `Exec` is parsed with the Desktop Entry quoting rules (double quotes, backslash escapes;
   unterminated quote = error) and field codes are dropped (`%u %U %f %F` become the URL for
   `open url`; with none, the URL is appended; `%%` → `%`).
5. Refused when any argument mentions one of those writable places (or starts with `~`), when a
   shell or interpreter (`sh`, `bash`, `zsh`, `fish`, `python*`, `perl`, `node`, …) appears with
   an inline-code flag (`-c`, `-lc`, `-e`, `-m`, `--eval`, …), or `env` gets `-S/--split-string`.
6. The program is an absolute path or is looked up only in `/usr/local/bin:/usr/bin`; refused
   when it (or its `realpath`) is in a writable place or this user can write it.

Flatpak exports (`/usr/bin/flatpak run …` from `/var/lib/flatpak/exports`) pass; user Flatpak
installs (`~/.local/share/flatpak`) and Brave/Chrome web apps (`~/.local/share/applications`)
are refused — open their URL instead.

**Why not `gtk-launch <id>`:** GIO re-resolves the id with its own rules and may D-Bus-activate
`DBusActivatable` apps through session service files under `~/.local/share/dbus-1/services`,
so what runs would not be what was checked. Exec'ing the checked argv removes that gap and the
re-read race. Cost: no D-Bus activation or startup notification (apps start the plain way).

## Checks (S12 (3), S13)

For `type` and `key`, in order:

1. Permission granted and not expired, else denied (“…ejecute /control on…”).
2. Input validation (above); lookalike letters (Cyrillic/Greek that turn the text or intent
   into a risky word, e.g. `sеnd` with a Cyrillic `е`) are **denied outright**.
3. Focus via `niri msg -j focused-window`: non-zero exit, timeout, bad JSON, or no focused
   window = denied.
4. Blocklist by `app_id`/title substrings: password managers (KeePassXC, Bitwarden, 1Password,
   Proton Pass, Enpass, …), keyrings and wallets, system settings, polkit/pinentry/askpass/gcr
   prompts, desktop portals, GParted; **and terminals** (foot, kitty, Alacritty, Ghostty,
   WezTerm, GNOME Console/Terminal, Konsole, xterm, …), because typing into a terminal would
   run commands outside the daemon's sandbox and could drive the Jarvis TUI itself (`/control on`).
5. Confirmation through `control.sock` **by the action, never by words** (S10a,
   `checks.confirm_reasons`). It asks when:
   * **Sending in a chat or mail** (“enviar en un chat o correo”): any Return keysym anywhere in
     the keys (`Return`, `ctrl+Return`, `KP_Enter`, `ISO_Enter`, …, any modifiers) or a newline
     in `text`, going to a mail or messaging app (Thunderbird, Evolution, Geary, Telegram,
     Signal, Discord, Slack, WhatsApp, Element, Teams, …) or to a browser (Brave, Firefox,
     Chromium/Chrome, Zen, …) whose window title shows web chat or mail (WhatsApp, Telegram,
     Gmail, Outlook, Discord, Messenger, Slack, Teams, Proton Mail, …; whole words, case and
     accents ignored). Return in a browser on any other page (search, forms) does not ask, nor
     in any other app.
   * **Clearing all content** (“borrar todo el contenido”): `ctrl+a` followed later in the same
     key sequence by `Delete`, `BackSpace` or `ctrl+x`; or Vim's `g g d shift+g`.
   * **Closing a window or app** (“cerrar la ventana”): `alt+F4`, `ctrl+w` or `ctrl+q` (extra
     modifiers included, e.g. `ctrl+shift+w`).

   Words in the text or the intent (“borrar”, “enviar”, “pagar”, …) never ask by themselves;
   the intent is only shown in the prompt. No subscriber = denied immediately; no answer within
   30 s = denied; subscribers all gone = denied.
6. Focus re-checked right before `wtype`: a different window id or a now-blocked window = denied.

With `window_id` (S16), steps 3–4 apply to the target window from `niri msg -j windows` instead
of the focused one, and the helper focuses it (after step 5) before step 6, which then compares
against the target id; focus is restored afterwards (see `type`/`key` with `window_id` above).

For `screenshot`: permission, then focus (unreadable = denied); a focused window on the
password/settings/security blocklist is refused (terminals are allowed, the TUI usually has the
focus). This addition is not required by S13: it keeps secrets out of captures in the common
case, but windows that are visible without focus are still captured. With `window_id` (S17)
the target from `niri msg -j windows` is checked instead, and re-checked once focused.

## Tests

```sh
cd host/input
python3 -m unittest discover -s tests        # or: <host venv>/bin/python -m pytest tests
```

The tests run the helper in-process on a temp `XDG_RUNTIME_DIR` with fake `wtype`, `grim`,
`niri`, `xdg-mime`, `playerctl` and `wpctl` scripts first on `PATH` that record their argv; `open` tests use a fake
`$HOME` and a fake fixed `PATH` with fake apps. `test_launch.py` checks the `open` rules on temp
dirs without running anything. No real Wayland tool or app runs. `test_main.py` starts `python3 -m jarvis_input` with `PATH=/nonexistent`.
`test_e2e.py` covers the pure parts of the desktop E2E runner below (it starts nothing).

## Desktop E2E (real wtype, browser and VS Code + Vim)

Automates the manual checks of `odd/tasks/s2-round2.md` L19-L27 (`odd/tasks/desktop-e2e.md`):

```sh
host/e2e/run.sh                 # = cd host/input && python3 -m jarvis_e2e
host/e2e/run.sh --no-vscode     # browser cases only (~6 s)
host/e2e/run.sh --keep -v       # keep the temp dir (client logs) and print tracebacks
```

How it works (`jarvis_e2e/`):

* `desktop.py` starts a **nested niri** (`niri -c <temp config>`: no autostart, no Xwayland, US
  layout) with its own `XDG_RUNTIME_DIR` under a temp dir, so it has its own Wayland and IPC
  sockets. niri 26.04 has no headless backend: the nested niri is one window on your desktop
  (it may take the focus while the run lasts, ~20 s). It runs with `vblank_mode=0`: when its
  window is not shown (monitors off, another workspace) the outer niri sends frame callbacks
  once a second and a vsynced nested niri then handled one key per second (keys repeated). Clients get the nested
  `WAYLAND_DISPLAY`/`NIRI_SOCKET` and no `DISPLAY`; they can never receive input from your
  session and your windows never receive input from them.
* Clients: Brave (or Chromium/Chrome) with a temp `--user-data-dir` showing two local pages
  (a textarea with a keydown logger, and an "other" window that plays the user's window), and
  VS Code (`/usr/share/code/code`) with a temp `--user-data-dir` and `--extensions-dir` holding a
  copy of `~/.vscode/extensions/vscodevim.vim-*`, editing a temp `doc.txt`.
* `run.py` runs the **real helper** (`jarvis_input.server.Helper`) in-process on the nested
  runtime dir, grants it through `control.sock`, and sends `focus`/`type`/`key`/`screenshot` requests with
  `window_id` through `agent.sock`, like the agent. So the wtype argv (`slots.type_argvs`,
  `slots.key_argv`, `parse_keys`, the Escape pause), the focus switch, the
  400 ms settle and the focus restore are the production code, run with the real `wtype` and
  `niri msg`. Before the helper starts, the runner refuses to go on unless `WAYLAND_DISPLAY`,
  `NIRI_SOCKET` and `XDG_RUNTIME_DIR` all point inside the temp dir and `DISPLAY` is unset.
* Results are read back over the Chrome DevTools Protocol (`--remote-debugging-port=0`, a
  stdlib WebSocket client in `cdp.py`): the textarea value and keydown log; in VS Code the
  rendered editor lines (`.view-line`, by position), the Vim mode from the status bar
  (`-- NORMAL --`/`-- INSERT --`) and, on failure, a capture-phase keydown log (key/code).

Cases (each starts with the "other" window focused through the helper's `focus`, and checks that
focus is back on it afterwards):

| Case | Checks |
| --- | --- |
| browser: type `hola mundo` | complete right after the window gets focus (L19-L22); every keydown on a character `code` |
| browser: type the L27 sentence + `’ — …` | complete text, legacy keysyms included, character codes only |
| browser: type ~100 distinct characters | complete text over several wtype runs, character codes only |
| browser: key `Escape g g d shift+g`, confirmed with a held `y` | asked (S10a); keydowns `Escape g g d G`, Escape with `code` `Escape` (L24-L25), letters on character codes; textarea `ggdG` |
| browser: key `ctrl+a Delete`, confirmed with a held `y` | asked (S10a); keydowns `a`/`KeyA` and `Delete`/`Delete`; textarea emptied |
| browser: key `Return` on a plain page | no confirmation (S10a; no subscriber, so a prompt would deny it); keydown `Enter`/`Enter` |
| browser: `screenshot` with `window_id` (T26) | the target page painted magenta is ≥ 5% of the PNG (decoded with stdlib `zlib`), `window_id` echoed; skipped without `grim` |
| vscode+vim: key `i`, type `hola mundo` | INSERT mode, buffer exactly `hola mundo`, character codes only (desktop-e2e L2) |
| vscode+vim: key `Escape g g d shift+g`, confirmed with a held `y` (every clear below too) | asked (S10a); buffer empty, NORMAL mode (L23-L25) |
| vscode+vim: key `i`, type the sentence | buffer exactly the sentence (L27) |
| vscode+vim: key `Escape g g d shift+g` again | buffer empty, NORMAL mode |
| vscode+vim: key `a`, then type ` Cada nuevo proyecto` right away | buffer exactly the text, INSERT mode (desktop-e2e L5-L7) |
| vscode+vim: key `Escape g g d shift+g` a third time | buffer empty, NORMAL mode |
| vscode+vim: key `a` with the production intent “… sin borrar contenido.”, then the text | no confirmation (S10a: words never ask); buffer exactly the text, INSERT mode (desktop-e2e L5-L8) |
| vscode+vim: key `Escape g g d shift+g` a fourth time | buffer empty, NORMAL mode |
| vscode+vim: key `i`, type `borrar todo, enviar y pagar` | no confirmation (S10a); buffer exactly the text |
| vscode+vim: key `Escape g g d shift+g` a fifth time | buffer empty, NORMAL mode |

Output: `PASS|FAIL|SKIP: <case> (<s>) — <detail>` per case, then
`summary: N passed, N failed, N skipped in Ns`. Exit **0** all ran cases passed, **1** a case
failed (or the desktop could not start: `FAIL: setup`, with the tails of the client logs), **77**
skipped (automake's SKIP code): no `WAYLAND_DISPLAY`, no `niri`/`wtype` on `PATH`, or no
Chromium-based browser; the reason is printed as `SKIP: requirements — …`. Missing VS Code or
vscodevim only skips the VS Code cases. Teardown (also on failure, Ctrl-C, SIGTERM or SIGHUP):
clients and the nested niri are killed by process group, the helper's sockets closed, and the
temp dir removed (unless `--keep`). Requirements: niri (as the running session), wtype, Brave or
Chromium, optionally VS Code at `/usr/share/code/code` with vscodevim; Python 3.11+, stdlib only.
CI (`.github/workflows/ci.yml`, job `host-input`) runs it too and accepts exit 0 or 77: GitHub
runners have no Wayland session, so there it prints `SKIP: requirements — no WAYLAND_DISPLAY…`.
Run it by hand on the niri desktop after changing `type`/`key`/focus code.
