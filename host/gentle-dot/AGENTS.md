# Jarvis desktop rules

You run as a background service inside the user's Wayland session (niri on Arch Linux).

## Opening graphical applications

- Never run a GUI program in the foreground (`brave`, `spotify`, `nautilus` …): it never
  exits, so the shell tool times out and you would wrongly report a failure.
- Launch it detached and return at once:
  `setsid -f <program> [args] >/dev/null 2>&1`
  or, when it has a desktop entry, `gtk-launch <desktop-id>` (e.g. `gtk-launch brave-browser`).
- Then confirm it started with `pgrep -f <program>` and report that it opened.
- To open a URL in the default browser: `setsid -f xdg-open '<url>' >/dev/null 2>&1`.
