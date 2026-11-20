"""An isolated desktop for the E2E run: a nested niri on its own runtime dir, and clients in it.

Nothing here talks to the user's session except the nested niri itself, which opens one winit
window on the outer compositor (niri 26.04 has no headless backend). Every client, wtype and
`niri msg` gets WAYLAND_DISPLAY/NIRI_SOCKET of the nested instance and no DISPLAY.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path

NIRI_CONFIG = """\
// jarvis-e2e: nested, no autostart, no Xwayland, no animations.
hotkey-overlay { skip-at-startup; }
prefer-no-csd
animations { off; }
xwayland-satellite { off; }
input { keyboard { xkb { layout "us"; } } }
"""

# Variables that could point a child at the user's session or change how it starts.
DROP_ENV = ("DISPLAY", "WAYLAND_SOCKET", "NIRI_SOCKET", "SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE",
            "XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID", "VSCODE_IPC_HOOK_CLI", "VSCODE_PID",
            "ELECTRON_RUN_AS_NODE", "GIO_LAUNCHED_DESKTOP_FILE", "TERM_PROGRAM")


class SetupError(Exception):
    """The isolated desktop could not be started (a FAIL of the run, not a SKIP)."""


def missing_requirements(env: Mapping[str, str], which: Callable[[str], str | None],
                         browser: str | None) -> str | None:
    """Why the run must be skipped, or None."""
    if not env.get("WAYLAND_DISPLAY"):
        return "no WAYLAND_DISPLAY: a nested niri needs a Wayland session to open its window"
    for tool in ("niri", "wtype"):
        if which(tool) is None:
            return f"{tool} not found on PATH"
    if browser is None:
        return "no Chromium-based browser found (brave, chromium, google-chrome-stable)"
    return None


def outer_wayland(env: Mapping[str, str]) -> str:
    """The user's WAYLAND_DISPLAY as an absolute path (the nested niri gets another runtime dir)."""
    name = env["WAYLAND_DISPLAY"]
    if os.path.isabs(name):
        return name
    return str(Path(env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / name)


def nested_sockets(runtime_dir: Path, names: list[str]) -> tuple[Path, Path] | None:
    """(wayland socket, niri IPC socket) among the entries of the nested runtime dir, or None."""
    wayland = sorted(n for n in names if n.startswith("wayland-") and not n.endswith(".lock"))
    niri = sorted(n for n in names if n.startswith("niri.") and n.endswith(".sock"))
    if not wayland or not niri:
        return None
    return runtime_dir / wayland[0], runtime_dir / niri[0]


def client_env(base: Mapping[str, str], runtime_dir: Path, wayland: Path, niri_sock: Path) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k not in DROP_ENV}
    env.update({"XDG_RUNTIME_DIR": str(runtime_dir), "WAYLAND_DISPLAY": str(wayland),
                "NIRI_SOCKET": str(niri_sock), "XDG_SESSION_TYPE": "wayland",
                "ELECTRON_OZONE_PLATFORM_HINT": "wayland"})
    return env


class Desktop:
    def __init__(self, root: Path, base_env: Mapping[str, str]) -> None:
        self.root = root
        self.base_env = dict(base_env)
        self.runtime_dir = root / "run"
        self.procs: list[tuple[str, subprocess.Popen[bytes]]] = []
        self.env: dict[str, str] = {}

    # --- processes -------------------------------------------------------------------------

    def spawn(self, name: str, argv: list[str], env: Mapping[str, str]) -> subprocess.Popen[bytes]:
        log = open(self.root / f"{name}.log", "wb")  # noqa: SIM115 (closed with the process)
        try:
            proc = subprocess.Popen(argv, env=dict(env), stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True, cwd=self.root)
        finally:
            log.close()
        self.procs.append((name, proc))
        return proc

    def close(self) -> None:
        """Kill clients first, then the compositor (each in its own process group)."""
        for _, proc in reversed(self.procs):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        for _, proc in reversed(self.procs):
            try:
                proc.wait(max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(2)
        self.procs.clear()

    # --- compositor ------------------------------------------------------------------------

    def start_niri(self, timeout: float = 15.0) -> None:
        os.mkdir(self.runtime_dir, 0o700)
        config = self.root / "niri.kdl"
        config.write_text(NIRI_CONFIG)
        env = {k: v for k, v in self.base_env.items() if k not in DROP_ENV}
        env["WAYLAND_DISPLAY"] = outer_wayland(self.base_env)
        env["XDG_RUNTIME_DIR"] = str(self.runtime_dir)
        proc = self.spawn("niri", ["niri", "-c", str(config)], env)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise SetupError(f"nested niri exited with {proc.returncode} (see niri.log)")
            found = nested_sockets(self.runtime_dir, os.listdir(self.runtime_dir))
            if found:
                self.env = client_env(self.base_env, self.runtime_dir, *found)
                if self.niri_json("outputs") is not None:
                    return
            time.sleep(0.1)
        raise SetupError("nested niri did not open its sockets in time")

    def niri(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["niri", "msg", *args], env=self.env, capture_output=True, text=True,
                              timeout=5, check=False)

    def niri_json(self, what: str) -> object:
        res = self.niri("-j", what)
        if res.returncode != 0:
            return None
        try:
            return json.loads(res.stdout)
        except ValueError:
            return None

    def windows(self) -> list[dict[str, object]]:
        data = self.niri_json("windows")
        return [w for w in data if isinstance(w, dict)] if isinstance(data, list) else []

    def focused_id(self) -> int | None:
        data = self.niri_json("focused-window")
        return data.get("id") if isinstance(data, dict) else None  # type: ignore[return-value]

    def wait_window(self, match: Callable[[dict[str, object]], bool], timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for w in self.windows():
                if match(w) and isinstance(w.get("id"), int):
                    return w["id"]  # type: ignore[return-value]
            time.sleep(0.2)
        raise SetupError("window did not appear: " + json.dumps(
            [(w.get("app_id"), w.get("title")) for w in self.windows()]))

    # --- clients ---------------------------------------------------------------------------

    def start_browser(self, browser: str, urls: list[str]) -> Path:
        profile = self.root / "browser-profile"
        profile.mkdir()
        argv = [browser, f"--user-data-dir={profile}", "--remote-debugging-port=0",
                "--remote-allow-origins=*", "--ozone-platform=wayland", "--no-first-run",
                "--no-default-browser-check", "--disable-sync", "--password-store=basic",
                "--disable-features=Translate,MediaRouter", "--disable-background-networking",
                "--disable-component-update", "--allow-file-access-from-files",
                "--disable-session-crashed-bubble", "--new-window", *urls]
        self.spawn("browser", argv, self.env)
        return profile

    def start_vscode(self, electron: Path, vim_ext: Path, file: Path) -> Path:
        data = self.root / "vscode-data"
        exts = self.root / "vscode-ext"
        (data / "User").mkdir(parents=True)
        exts.mkdir()
        shutil.copytree(vim_ext, exts / vim_ext.name, symlinks=True)
        (data / "User" / "settings.json").write_text(json.dumps({
            "workbench.startupEditor": "none", "security.workspace.trust.enabled": False,
            "telemetry.telemetryLevel": "off", "update.mode": "none",
            "extensions.autoCheckUpdates": False, "extensions.autoUpdate": False,
            "workbench.tips.enabled": False, "editor.minimap.enabled": False,
            "window.restoreWindows": "none", "workbench.enableExperiments": False,
            "chat.disableAIFeatures": True, "files.hotExit": "off",
            "window.title": "jarvis-e2e-vscode ${activeEditorShort}",
        }, indent=1))
        argv = [str(electron), f"--user-data-dir={data}", f"--extensions-dir={exts}",
                "--remote-debugging-port=0", "--ozone-platform=wayland", "--password-store=basic",
                "--disable-workspace-trust", "--skip-release-notes", "--skip-welcome",
                "--disable-telemetry", "--new-window", str(file)]
        self.spawn("vscode", argv, self.env)
        return data
