# host/systemd: units and installer

Tasks P3, T3, T13, T14, T21 (S13) and T24 (S15). `install.sh` installs these user units. `test_install.sh`
checks it, with a `systemctl` stub and a sandboxed `HOME`.

| File | Installed as | Role |
|---|---|---|
| `gentle-dot.service.in` | `~/.config/systemd/user/gentle-dot.service` (rendered) | Gentle Dot daemon, **sandboxed**: no display |
| `jarvis-input.service.in` | `~/.config/systemd/user/jarvis-input.service` (verbatim) | `jarvis-input` helper, the only process with the display |
| `jarvis-sandbox-exec` | `~/.local/share/jarvis/sandbox/jarvis-sandbox-exec` | Landlock wrapper the daemon starts through |
| `../input/jarvis_input/` | `~/.local/share/jarvis/input/jarvis_input/` | helper package (`python3 -I`, see the unit) |
| `../jarvis/` + `../requirements.txt` | `~/.local/share/jarvis/tui/{jarvis/,requirements.txt,.venv/}` | the TUI, with its own venv (`/usr/bin/python3 -m venv` + pip) |
| (rendered) | `~/.local/bin/jarvis` | TUI launcher: `.venv/bin/python -I` on the installed copy (the old one goes to `jarvis.bak`) |
| — | `~/Jarvis/` (0700, created when missing) | the agent's **work folder**: with `~/.gentle-dot`, the only place in the home it can write |

```sh
host/systemd/install.sh --gentle-dot-dir DIR --check      # verify only
host/systemd/install.sh --gentle-dot-dir DIR --dry-run    # print, write nothing
host/systemd/install.sh --gentle-dot-dir DIR --enable     # install, enable and (re)start both
host/systemd/install.sh --uninstall                       # remove both units; keep files
bash host/systemd/test_install.sh                         # offline checks
```

Every changed file is backed up first (`.bak`; the helper package goes to `jarvis_input.bak/`, the
TUI package to `tui/jarvis.bak/`, a replaced venv to `tui/.venv.bak/`). The TUI venv is rebuilt only
when `host/requirements.txt` changes; it is built beside the old one and swapped in only if pip
succeeds, so offline the previous venv stays (the script warns and the next run retries). The TUI
is not a service: reopen it after an update.
With `--enable`, the helper starts first. A helper change only restarts the helper. A change to
the daemon's unit, rules or extension restarts the daemon. Without `--enable`, the script prints
the restart command. **The sandbox takes effect when the daemon starts.** A daemon that was
already running keeps its old, unsandboxed view until it restarts.

## Daemon sandbox (S13)

Goal: neither the daemon nor any shell its agent opens can reach the display or the control
plane. It reaches GUI actions only through `$XDG_RUNTIME_DIR/jarvis/agent.sock`.

A user manager is unprivileged. `systemd.exec(5)`, section SANDBOXING, says namespace options
"will work when used in conjunction with PrivateUsers=true". `TemporaryFileSystem=` turns
`PrivateUsers=` on by itself in user units. It needs `kernel.unprivileged_userns_clone=1`,
which is set on this machine.

| Directive | Why |
|---|---|
| `PrivateUsers=yes` | Makes the mount namespaces below possible in a user unit. It also blocks `/proc/<niri-pid>/root/run/user/1000/wayland-1` (EACCES): processes outside the unit are in the parent user namespace. |
| `TemporaryFileSystem=%t:mode=0700` | Empty private `$XDG_RUNTIME_DIR`. It hides `wayland-*` and `niri.*.sock`, whatever their names. It also hides `bus`, `at-spi` (which can synthesize keys), `pipewire-0`, the gnupg and ssh agents, and `systemd/private` (so no `systemctl --user` or `systemd-run --user` escape). The daemon uses nothing in `%t`: upstream has no `XDG_RUNTIME_DIR` use, and its open sockets are socketpairs and TCP 4317. |
| `RuntimeDirectory=jarvis`, `RuntimeDirectoryMode=0700`, `RuntimeDirectoryPreserve=yes` | Makes `%t/jarvis` exist before the namespace is built, so the bind always has a source. It is shared with `jarvis-input.service`. Both units preserve it: if one unit removed it, the other unit's bind would be left stale (observed in a probe). |
| `BindReadOnlyPaths=%t/jarvis` | The only part of `%t` the daemon sees. The bind is read-only, so the agent cannot swap in its own socket. `connect()` still works on a read-only mount, and the helper's sockets, even ones it recreates later, show through the bind. |
| `InaccessiblePaths=-%t/bus -%t/jarvis-control -%t/systemd -/tmp/.X11-unix -/run/docker.sock -/run/dbus/system_bus_socket` | Defense in depth in case a drop-in resets the tmpfs. `/tmp/.X11-unix` is outside `%t`. The user is in the `docker` group, and Docker's `/_ping` answered from a plain `PrivateUsers` unit. S15 adds the system bus (logind, NetworkManager, udisks, polkit). |
| `ProtectHome=read-only` (S15) | The whole home is read-only: what the helper may launch (`~/.local/bin`, AppImages, `~/.local/share/applications`, `mimeapps.list`, `~/.config/fish`), the helper, wrapper and TUI under `~/.local/share/jarvis`, systemd drop-ins (`~/.config/systemd/user`, `~/.local/share/systemd`, `environment.d`) and the niri config. The verifier of S13 found each of these writable (L17). `%t` stays the private tmpfs. |
| `ReadWritePaths=%h/.gentle-dot %h/Jarvis` (S15) | The daemon's data dir (`GENTLE_DOT_DATA_DIR` default: token, sessions, `state.json`, the engine's `HOME` = `~/.gentle-dot/home` with its XDG dirs and `TMPDIR`, the agent dir and its npm packages) and the work folder. No `-`: a missing path fails the start with 226/NAMESPACE (probed), so `install.sh` creates both (0700). |
| `PrivateDevices=yes` | logind gives the seat user an ACL on `/dev/uinput`, which is enough to create a virtual keyboard. It opened from a plain `PrivateUsers` unit. |
| `ReadOnlyPaths=<unit dir> <jarvis-stop> <jarvis launcher> <agent>/extensions <agent>/AGENTS.md` | The home entries of S13 (`~/.local/share/jarvis`, `~/.local/share/applications`) are dropped: `ProtectHome=` covers them. The unit dir, `jarvis-stop` and the launcher stay because `XDG_CONFIG_HOME` and `--bin-dir` may point outside the home; the agent's extensions and rules stay because they sit inside the writable `~/.gentle-dot` (a nested read-only path wins: probed). There is **no `-` prefix**: systemd skips a missing `-` path, and the agent could then create it. `install.sh` creates every path before any restart. |
| `UnsetEnvironment=WAYLAND_DISPLAY DISPLAY NIRI_SOCKET DBUS_SESSION_BUS_ADDRESS` | Programs do not even try the session. |
| `jarvis-sandbox-exec` (Landlock ABI ≥ 6, scopes `ABSTRACT_UNIX_SOCKET` and `SIGNAL`) | Xwayland (started by niri) also listens on the **abstract** socket `@/tmp/.X11-unix/X0` with no authentication. Abstract sockets live in the network namespace, so no path directive hides them. `PrivateNetwork=` would cut off the internet and port 4317. The wrapper fails closed: on an older kernel the daemon does not start. The signal scope also stops the agent from killing the helper or niri. |

The helper unit keeps the real runtime directory: there is no `PrivateTmp`, `PrivateUsers` or
`TemporaryFileSystem`, because wtype, grim and niri need the Wayland and niri sockets. It runs with
a minimal environment (S15): `PATH=/usr/local/bin:/usr/bin`, `UnsetEnvironment=LD_PRELOAD
LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE PYTHONINSPECT
NODE_OPTIONS` (the apps it opens inherit it), and `python3 -I -B -c "…runpy.run_module('jarvis_input')"`:
`-I` ignores every `PYTHON*` variable, the user site-packages and the current directory, so the
package directory is put on `sys.path` explicitly. It uses `KillMode=process`, so apps it opened
survive a restart of the helper.

The TUI and `jarvis-stop` (S15) are out of the agent's reach too: the TUI runs from its installed
copy with its own venv (`~/.local/bin/jarvis` → `.venv/bin/python -I`, the copy put on `sys.path`
by hand), and `jarvis-stop` starts with `#!/bin/bash`, sets `PATH=/usr/local/bin:/usr/bin` before
anything else and runs `/usr/bin/python3`. `install.sh` prints the niri bind with the absolute
path: `Alt+Shift+Escape { spawn "/home/<user>/.local/bin/jarvis-stop"; }`.

## Probe evidence (2026-11-20, systemd 262, Linux 7.3, transient `systemd-run --user` units)

These probes ran with the same properties, with `%t` written out as `/run/user/1000` because
transient units do not expand it, plus the wrapper. Results:

- `$XDG_RUNTIME_DIR` contains only the bound directory. The display variables are unset.
- `/proc/<niri>/root/run/user/1000/`: permission denied. `/dev/uinput`: absent.
  `/tmp/.X11-unix`: permission denied. `systemctl --user`: "Failed to connect to user scope
  bus".
- Abstract X11: EPERM under the wrapper. Without it the connection succeeds and the X11 setup
  returns success, so there is no auth. Docker: EACCES. Signal to niri: EPERM.
- The internet works. A server inside on 127.0.0.1 can be reached from the host. A socket in
  the bound directory answers through the read-only bind. The real daemon's `--self-check`
  passes inside.
- `systemd-analyze --user verify` accepts both rendered units (`test_install.sh` §9).

S15 probes (same day, transient units, only a temporary directory under `/tmp`, writability
checked with `access(W_OK)` so nothing was created in the home), with `PrivateUsers=yes`,
`ProtectHome=read-only`, the `%t` tmpfs and bind, `ReadWritePaths=~/.gentle-dot <tmp>/ro/rw`,
`ReadOnlyPaths=<tmp>/ro ~/.gentle-dot/agent/extensions ~/.gentle-dot/agent/AGENTS.md` and the
system-bus entry:

- Read-only: `~`, `~/.config`, `~/.config/systemd/user`, `~/.local/bin`, `~/.local/share/jarvis`,
  `~/.engram`, the Gentle Dot checkout. Writable: `~/.gentle-dot`, `~/.gentle-dot/agent`. Read-only
  again inside it: `agent/extensions`, `agent/AGENTS.md`. In `/tmp`: a write under the nested
  `rw` succeeded, one beside it failed with "Read-only file system".
- `%t` still holds only `jarvis`. `/run/dbus/system_bus_socket`: `PermissionError` (EACCES).
- mise's node 24 runs; the real daemon's `--self-check` through the wrapper passes (exit 0).
- A missing `ReadWritePaths=` entry without `-`: "Failed at step NAMESPACE", status 226.
- Helper form: `python3 -I -B -c "…run_module('jarvis_input')"` imports the package, runs it as
  `__main__` with `isolated=1`, writes no `__pycache__`; `Environment=LD_PRELOAD=…` and
  `PYTHONPATH=…` set in the same unit are removed by `UnsetEnvironment=`.
- A venv built in a staging directory and then moved still works (`bin/python -I`, `sys.prefix` follows).

What upstream writes in the home (`f97d56d`, `packages/daemon/src`): only the data dir
(`config.ts`: `GENTLE_DOT_DATA_DIR ?? "~/.gentle-dot"`); the engine gets `HOME=~/.gentle-dot/home`
and XDG/`TMPDIR` under it (`isolation.ts`). It reads, never writes, `~/.pi/gentle-ai/profiles.json`
(profile import). Memory: by default the engine uses the user's Engram (`ENGRAM_DATA_DIR=~/.engram`)
through the server on 127.0.0.1:7437, started outside the unit. If that server is not running, the
engine may try to start one inside the sandbox, which cannot write `~/.engram`, so memory would be
off (by design: the agent cannot rewrite the user's memory files).

## Manual verification after installing (for the person who installs)

```sh
host/systemd/install.sh --gentle-dot-dir ~/devwork/.projects/apps/_scratch-gentle-dot --enable
systemctl --user status jarvis-input gentle-dot --no-pager
systemctl --user show gentle-dot -p PrivateUsers -p PrivateDevices -p TemporaryFileSystem \
  -p BindReadOnlyPaths -p InaccessiblePaths -p ReadOnlyPaths -p UnsetEnvironment
pid=$(systemctl --user show -p MainPID --value gentle-dot)
tr '\0' '\n' </proc/$pid/environ | grep -E '^(WAYLAND_DISPLAY|DISPLAY|NIRI_SOCKET|DBUS_SESSION_BUS_ADDRESS)=' || echo "sin variables de sesión"
grep -E '^(NoNewPrivs|Seccomp)' /proc/$pid/status      # NoNewPrivs: 1
```

Then ask Jarvis, in the TUI, to run these through its `bash` tool. The commands only read:

```sh
ls -A "$XDG_RUNTIME_DIR"                    # expected: only jarvis
ls /tmp/.X11-unix; ls /dev/uinput            # expected: permission denied / absent
systemctl --user is-active jarvis-input      # expected: Failed to connect to user scope bus
python3 -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect("\0/tmp/.X11-unix/X0")'   # expected: PermissionError (EPERM)
touch ~/.local/share/applications/x.desktop  # expected: Read-only file system
touch ~/.local/bin/x ~/.config/x             # expected: Read-only file system (S15)
touch ~/Jarvis/prueba && rm ~/Jarvis/prueba  # expected: works (the work folder)
python3 -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect("/run/dbus/system_bus_socket")'   # expected: PermissionError
```

Check the TUI from a terminal: `jarvis` opens the installed copy (`head -n 8 ~/.local/bin/jarvis`
shows the launcher; `~/.local/bin/jarvis.bak` is the old checkout launcher).

Also confirm that Gentle Dot still works: chat, `/login` and the model list.

If the daemon does not start, run `journalctl --user -u gentle-dot -n 50`. Two messages point to
the sandbox:

- "Failed to set up mount namespacing" means a `ReadOnlyPaths=` or `ReadWritePaths=` path is missing (for example `~/Jarvis` was removed): run
  `install.sh` again.
- "jarvis-sandbox-exec: el kernel no ofrece Landlock" means the kernel has no Landlock scopes.
