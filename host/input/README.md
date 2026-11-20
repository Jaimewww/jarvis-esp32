# jarvis-input — the only process that touches the screen (S13)

`jarvis-input` is a small Python 3 service (standard library only, `asyncio`). It is the
**only** process that runs `wtype`, `grim`, `niri msg` and `xdg-mime`, and that launches apps
(S15: it execs the checked `Exec` itself; no `gtk-launch`, no generic `xdg-open`). The
Gentle Dot daemon is sandboxed away from Wayland and asks this helper over a Unix socket; the
Jarvis TUI and `jarvis-stop` grant, revoke and confirm over a second socket the daemon cannot
reach. It replaces the S12 permission file, which the agent could forge.

* `jarvis_input/checks.py` — pure checks: key parsing (ported from
  `host/gentle-dot/extensions/pc-control-core.ts`), text limits, risky words, blocklist,
  Return-confirmation apps, `open` validation, rate limiter.
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
| `{"op":"apps","query":"notas"}` (S14) | no | `{"ok":true,"apps":[{"id":"<desktop-id>","name":"…"}]}` |
| `{"op":"windows"}` (S14) | yes | `{"ok":true,"windows":[{"id":11,"app_id":"…","title":"…","focused":false}]}` |
| `{"op":"focus","window_id":11}` (S14) | yes | `{"ok":true}` |

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
  `intent` optional, ≤ 500 characters. Runs `wtype -- <text>`.
* `key`: up to 20 space-separated combos `mod+mod+key`; modifiers `ctrl|control`, `shift`,
  `alt`, `altgr`, `super|logo|win|meta|mod`; aliases `enter|return`, `esc`, `tab`, `space`,
  `backspace`, `delete|del`, `insert`, `home`, `end`, `pageup`, `pagedown`, arrows, `f1`–`f24`;
  otherwise an xkb keysym name. Runs `wtype -M mod … -k key … -m mod …`.
* `screenshot`: optional `scale` in `[0.05, 1]`; runs `grim [-s <scale>] -`.
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
  checks (listed, not blocklisted, not a terminal); risky-word and Return-in-browser
  confirmations name the **target** window and are asked before anything is focused (so the user
  can answer in the TUI); then the currently focused window id is remembered, the target is
  focused and confirmed (≤ 500 ms), focus is re-checked against the target right before `wtype`,
  `wtype` runs, and finally (also when a later step failed) focus goes back to the remembered
  window if it still exists and is not the target — best effort, after a 100 ms pause; failures
  are only logged. The response echoes `window_id`. Without `window_id` nothing changes. Agent
  flow for typing: `apps` + `open` (or `windows`), then `type`/`key` with `window_id`; `focus`
  only to bring a window forward for the user.
* `windows`, `focus`, `type`, `key` and `screenshot` run one at a time (an action lock).

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
  (that request answers with an error) and denies every pending confirmation.
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
5. Confirmation through `control.sock` when the text or intent has a word starting with a
   risky stem (`envi`, `pag`, `compr`, `elimin`, `borr`, `submit`, `send`, `pay`, `buy`,
   `delet`; text is NFKC-normalized, zero-width/format characters dropped, accents stripped,
   case-folded), or when Return/KP_Enter (any modifiers, or a newline in `text`) goes to a
   browser, mail or messaging app (Brave, Firefox, Chromium/Chrome, Zen, Thunderbird,
   Evolution, Telegram, Signal, Discord, Slack, WhatsApp, Element, Teams, …). No subscriber =
   denied immediately; no answer within 30 s = denied; subscribers all gone = denied.
6. Focus re-checked right before `wtype`: a different window id or a now-blocked window = denied.

With `window_id` (S16), steps 3–4 apply to the target window from `niri msg -j windows` instead
of the focused one, and the helper focuses it (after step 5) before step 6, which then compares
against the target id; focus is restored afterwards (see `type`/`key` with `window_id` above).

For `screenshot`: permission, then focus (unreadable = denied); a focused window on the
password/settings/security blocklist is refused (terminals are allowed, the TUI usually has the
focus). This addition is not required by S13: it keeps secrets out of captures in the common
case, but windows that are visible without focus are still captured.

## Tests

```sh
cd host/input
python3 -m unittest discover -s tests        # or: <host venv>/bin/python -m pytest tests
```

The tests run the helper in-process on a temp `XDG_RUNTIME_DIR` with fake `wtype`, `grim`,
`niri` and `xdg-mime` scripts first on `PATH` that record their argv; `open` tests use a fake
`$HOME` and a fake fixed `PATH` with fake apps. `test_launch.py` checks the `open` rules on temp
dirs without running anything. No real Wayland tool or app runs. `test_main.py` starts `python3 -m jarvis_input` with `PATH=/nonexistent`.
