"""The jarvis-input sockets end to end (S13), with fake wtype/grim/niri/xdg-mime on PATH.

The helper runs in-process on a temp XDG_RUNTIME_DIR; the fakes are tiny Python scripts that record
their argv in ``calls.jsonl``. No real Wayland tool is ever run.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_input import server  # noqa: E402

FAKE = f"""#!{sys.executable}
import json, os, sys, time
name = os.path.basename(sys.argv[0])
d = os.environ["FAKE_DIR"]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps({{"name": name, "argv": sys.argv[1:], "pid": os.getpid(),
                        "new_session": os.getsid(0) == os.getpid(), "cwd": os.getcwd(),
                        "path": os.environ.get("PATH"), "env": sorted(os.environ)}}) + "\\n")
def read(n, default=None):
    try:
        with open(os.path.join(d, n)) as f:
            return f.read()
    except FileNotFoundError:
        return default
if name == "niri" and "windows" in sys.argv:
    item = json.loads(read("windows.json", '{{"stdout": "[]"}}'))
    sys.stdout.write(item.get("stdout", ""))
    sys.exit(item.get("exit", 0))
if name == "niri" and "focused-output" in sys.argv:
    item = json.loads(read("output.json", '{{"stdout": "null"}}'))
    sys.stdout.write(item.get("stdout", ""))
    sys.exit(item.get("exit", 0))
if name == "niri" and "action" in sys.argv:
    sys.exit(int(read("niri_action_exit_" + sys.argv[-1], read("niri_action_exit", "0"))))
if name == "niri":
    path = os.path.join(d, "focus.json")
    with open(path) as f:
        seq = json.load(f)
    item = seq[0]
    if len(seq) > 1:
        with open(path, "w") as f:
            json.dump(seq[1:], f)
    sys.stdout.write(item.get("stdout", ""))
    sys.exit(item.get("exit", 0))
if name == "xdg-mime":
    sys.stdout.write(read("xdg-mime_out", ""))
if name == "grim":
    with open(os.path.join(d, "screen.png"), "rb") as f:
        sys.stdout.buffer.write(f.read())
if name == "wtype" and read("wtype_sleep"):
    time.sleep(float(read("wtype_sleep")))
sys.exit(int(read(name + "_exit", "0")))
"""

EDITOR = {"id": 7, "app_id": "org.gnome.TextEditor", "title": "Notas"}
BRAVE = {"id": 8, "app_id": "brave-browser", "title": "Inbox"}
PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


class Base(unittest.IsolatedAsyncioTestCase):
    confirm_timeout = 2.0
    idle_timeout = 5.0

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.runtime = root / "run"
        self.runtime.mkdir(mode=0o700)
        self.fake = root / "fake"
        self.fake.mkdir()
        bindir = root / "bin"
        bindir.mkdir()
        for name in ["wtype", "grim", "niri", "gtk-launch", "xdg-open", "xdg-mime"]:
            p = bindir / name
            p.write_text(FAKE)
            p.chmod(0o755)
        (self.fake / "screen.png").write_bytes(PNG)
        self.focus(EDITOR)
        self.env = mock.patch.dict(os.environ, {
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_DIR": str(self.fake),
            "XDG_RUNTIME_DIR": str(self.runtime),
        })
        self.env.start()
        self.helper = server.Helper(self.runtime, confirm_timeout=self.confirm_timeout,
                                    idle_timeout=self.idle_timeout)
        await self.helper.start()
        self.agent_path = self.runtime / "jarvis" / "agent.sock"
        self.control_path = self.runtime / "jarvis-control" / "control.sock"
        self.conns: list[asyncio.StreamWriter] = []

    async def asyncTearDown(self) -> None:
        for w in self.conns:
            w.close()
        await self.helper.close()
        self.env.stop()
        self.tmp.cleanup()

    # fakes ---------------------------------------------------------------
    def focus(self, *windows: Any) -> None:
        """Successive answers of `niri msg -j focused-window` (the last one repeats)."""
        seq = []
        for w in windows:
            if isinstance(w, dict) and ("exit" in w or "stdout" in w):
                seq.append(w)
            else:
                seq.append({"stdout": json.dumps(w)})
        (self.fake / "focus.json").write_text(json.dumps(seq))

    def windows(self, *windows: dict[str, Any], exit: int = 0, raw: str | None = None) -> None:
        """The answer of `niri msg -j windows` (focused = is_focused)."""
        out = raw if raw is not None else json.dumps(
            [{"id": w["id"], "app_id": w["app_id"], "title": w["title"], "is_focused": w.get("focused", False),
              "workspace_id": 1} for w in windows])
        (self.fake / "windows.json").write_text(json.dumps({"stdout": out, "exit": exit}))

    def calls(self, name: str | None = None) -> list[dict[str, Any]]:
        p = self.fake / "calls.jsonl"
        if not p.exists():
            return []
        rows = [json.loads(l) for l in p.read_text().splitlines()]
        return [r for r in rows if name is None or r["name"] == name]

    # sockets -------------------------------------------------------------
    async def connect(self, path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        r, w = await asyncio.open_unix_connection(str(path), limit=1 << 22)
        self.conns.append(w)
        return r, w

    async def ask(self, path: Path, req: Any, timeout: float = 5.0) -> dict[str, Any]:
        r, w = await self.connect(path)
        return await self.ask_on(r, w, req, timeout)

    async def ask_on(self, r: asyncio.StreamReader, w: asyncio.StreamWriter, req: Any,
                     timeout: float = 5.0) -> dict[str, Any]:
        line = req if isinstance(req, bytes) else (json.dumps(req) + "\n").encode()
        w.write(line)
        await w.drain()
        return json.loads(await asyncio.wait_for(r.readline(), timeout))

    async def agent(self, req: Any, timeout: float = 5.0) -> dict[str, Any]:
        return await self.ask(self.agent_path, req, timeout)

    async def control(self, req: Any) -> dict[str, Any]:
        return await self.ask(self.control_path, req)

    async def grant(self, minutes: float = 30) -> dict[str, Any]:
        res = await self.control({"op": "grant", "minutes": minutes})
        self.assertTrue(res["ok"], res)
        return res

    async def subscribe(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        r, w = await self.connect(self.control_path)
        res = await self.ask_on(r, w, {"op": "subscribe"})
        self.assertEqual(res, {"ok": True})
        state = json.loads(await asyncio.wait_for(r.readline(), 2))
        self.assertEqual(state["event"], "state")
        return r, w

    async def next_event(self, r: asyncio.StreamReader, timeout: float = 3.0) -> dict[str, Any]:
        return json.loads(await asyncio.wait_for(r.readline(), timeout))


class StartupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def test_runtime_dir_from_env(self) -> None:
        with self.assertRaises(server.StartupError):
            server.runtime_dir_from_env({})
        with self.assertRaises(server.StartupError):
            server.runtime_dir_from_env({"XDG_RUNTIME_DIR": ""})
        with self.assertRaises(server.StartupError):
            server.runtime_dir_from_env({"XDG_RUNTIME_DIR": "run/user/1000"})
        with self.assertRaises(server.StartupError):
            server.runtime_dir_from_env({"XDG_RUNTIME_DIR": str(self.runtime / "missing")})
        self.assertEqual(server.runtime_dir_from_env({"XDG_RUNTIME_DIR": str(self.runtime)}), self.runtime)

    async def test_modes(self) -> None:
        (self.runtime / "jarvis").mkdir(mode=0o755)
        os.chmod(self.runtime / "jarvis", 0o755)
        h = server.Helper(self.runtime)
        await h.start()
        try:
            for d in ["jarvis", "jarvis-control"]:
                self.assertEqual(stat.S_IMODE(os.stat(self.runtime / d).st_mode), 0o700)
            for s in ["jarvis/agent.sock", "jarvis-control/control.sock"]:
                st = os.lstat(self.runtime / s)
                self.assertTrue(stat.S_ISSOCK(st.st_mode))
                self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        finally:
            await h.close()
        self.assertFalse((self.runtime / "jarvis" / "agent.sock").exists())

    async def test_refuses_symlinked_dir(self) -> None:
        real = self.runtime / "elsewhere"
        real.mkdir(mode=0o700)
        (self.runtime / "jarvis-control").symlink_to(real)
        with self.assertRaises(server.StartupError):
            await server.Helper(self.runtime).start()
        self.assertEqual(list(real.iterdir()), [])

    async def test_refuses_non_dir(self) -> None:
        (self.runtime / "jarvis").write_text("x")
        with self.assertRaises(server.StartupError):
            await server.Helper(self.runtime).start()

    async def test_removes_stale_socket(self) -> None:
        d = self.runtime / "jarvis"
        d.mkdir(mode=0o700)
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(d / "agent.sock"))
        s.close()  # bound, never listening: stale
        h = server.Helper(self.runtime)
        await h.start()
        await h.close()

    async def test_refuses_live_socket(self) -> None:
        first = server.Helper(self.runtime)
        await first.start()
        try:
            with self.assertRaises(server.StartupError):
                await server.Helper(self.runtime).start()
            # the first one still answers
            r, w = await asyncio.open_unix_connection(str(self.runtime / "jarvis-control" / "control.sock"))
            w.write(b'{"op":"status"}\n')
            self.assertTrue(json.loads(await asyncio.wait_for(r.readline(), 3))["ok"])
            w.close()
        finally:
            await first.close()

    async def test_refuses_non_socket_at_path(self) -> None:
        d = self.runtime / "jarvis"
        d.mkdir(mode=0o700)
        (d / "agent.sock").write_text("not a socket")
        with self.assertRaises(server.StartupError):
            await server.Helper(self.runtime).start()
        self.assertEqual((d / "agent.sock").read_text(), "not a socket")


class ProtocolTest(Base):
    idle_timeout = 0.5

    async def test_errors_keep_connection(self) -> None:
        r, w = await self.connect(self.control_path)
        for bad in [b"{nope\n", b"[1,2]\n", b'"x"\n', b"{}\n", b'{"op":"fly"}\n', b'{"op":3}\n']:
            res = await self.ask_on(r, w, bad)
            self.assertFalse(res["ok"], bad)
            self.assertIsInstance(res["error"], str)
        self.assertTrue((await self.ask_on(r, w, {"op": "status"}))["ok"])

    async def test_ops_are_split_by_socket(self) -> None:
        for op in ["grant", "revoke", "status", "subscribe", "answer"]:
            res = await self.agent({"op": op, "minutes": 30, "id": "x", "allow": True})
            self.assertFalse(res["ok"], op)
        self.assertFalse((await self.control({"op": "status"}))["enabled"])
        for op in ["open", "type", "key", "screenshot"]:
            res = await self.control({"op": op, "app": "x", "text": "a", "keys": "a"})
            self.assertFalse(res["ok"], op)
        self.assertEqual(self.calls(), [])

    async def test_oversized_request(self) -> None:
        r, w = await self.connect(self.agent_path)
        w.write(b'{"op":"type","text":"' + b"a" * (70 * 1024) + b'"}\n')
        await w.drain()
        res = json.loads(await asyncio.wait_for(r.readline(), 3))
        self.assertFalse(res["ok"])
        self.assertEqual(await asyncio.wait_for(r.read(), 3), b"")  # closed
        self.assertEqual(self.calls(), [])

    async def test_idle_timeout_closes(self) -> None:
        r, _ = await self.connect(self.agent_path)
        self.assertEqual(await asyncio.wait_for(r.read(), 3), b"")

    async def test_subscriber_is_not_idle_closed(self) -> None:
        r, _ = await self.subscribe()
        await asyncio.sleep(0.8)
        await self.grant()
        self.assertEqual((await self.next_event(r))["enabled"], True)


class ControlTest(Base):
    async def test_status_grant_revoke(self) -> None:
        self.assertEqual(await self.control({"op": "status"}), {"ok": True, "enabled": False, "expires_at": None})
        before = time.time()
        res = await self.grant(30)
        self.assertEqual(set(res), {"ok", "expires_at"})
        self.assertTrue(res["expires_at"].endswith("Z"))
        exp = server.parse_iso(res["expires_at"])
        self.assertAlmostEqual(exp - before, 1800, delta=5)
        st = await self.control({"op": "status"})
        self.assertEqual(st, {"ok": True, "enabled": True, "expires_at": res["expires_at"]})
        self.assertEqual(await self.control({"op": "revoke"}), {"ok": True})
        self.assertFalse((await self.control({"op": "status"}))["enabled"])

    async def test_grant_default_and_cap(self) -> None:
        now = time.time()
        res = await self.control({"op": "grant"})
        self.assertAlmostEqual(server.parse_iso(res["expires_at"]) - now, 1800, delta=5)
        res = await self.control({"op": "grant", "minutes": 600})
        self.assertTrue(res["ok"])
        self.assertLessEqual(server.parse_iso(res["expires_at"]) - now, 1800 + 5)

    async def test_grant_rejects_bad_minutes(self) -> None:
        for bad in [0, -1, True, "30", None, float("nan"), [30]]:
            res = await self.control({"op": "grant", "minutes": bad})
            self.assertFalse(res["ok"], bad)
        self.assertFalse((await self.control({"op": "status"}))["enabled"])

    async def test_state_events(self) -> None:
        r, _ = await self.subscribe()
        res = await self.grant()
        self.assertEqual(await self.next_event(r), {"event": "state", "enabled": True, "expires_at": res["expires_at"]})
        await self.control({"op": "revoke"})
        self.assertEqual(await self.next_event(r), {"event": "state", "enabled": False, "expires_at": None})

    async def test_expiry(self) -> None:
        r, _ = await self.subscribe()
        await self.grant(0.01)  # 0.6 s
        self.assertTrue((await self.next_event(r))["enabled"])
        self.assertEqual(await self.next_event(r, 3), {"event": "state", "enabled": False, "expires_at": None})
        res = await self.agent({"op": "type", "text": "hola", "intent": "x"})
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_expiry_checked_on_request(self) -> None:
        await self.grant()
        self.helper._expires_mono = time.monotonic() - 1  # the timer has not fired yet
        res = await self.agent({"op": "type", "text": "hola", "intent": "x"})
        self.assertFalse(res["ok"])
        self.assertFalse((await self.control({"op": "status"}))["enabled"])


class OpenTest(Base):
    """S15: `open` resolves the entry itself and execs its Exec; no gtk-launch, no generic xdg-open."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        root = Path(self.tmp.name).resolve()
        self.home = root / "home"
        self.sysbin = root / "usr" / "bin"  # stands in for the fixed /usr/local/bin:/usr/bin
        self.sys_data = root / "usr" / "share"
        self.fp = root / "var" / "lib" / "flatpak" / "exports" / "share"
        for d in (self.home / ".local" / "bin", self.sysbin, self.sys_data / "applications",
                  self.home / ".local" / "share" / "applications", self.fp / "applications"):
            d.mkdir(parents=True)
        for p in (self.sysbin / "brave", self.sysbin / "flatpak", self.home / ".local" / "bin" / "evil"):
            p.write_text(FAKE)
            p.chmod(0o755)
        os.environ.pop("XDG_DATA_HOME", None)
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_DATA_DIRS"] = str(self.sys_data)
        os.environ["LD_PRELOAD"] = "/home/evil.so"
        self.patches = [mock.patch.object(server.launch, "LAUNCH_PATH", str(self.sysbin)),
                        mock.patch.object(server.launch, "SYSTEM_UNTRUSTED", ()),
                        mock.patch.object(server.launch, "is_writable", lambda p: False),
                        mock.patch.object(server.checks, "FLATPAK_SYSTEM_EXPORTS", self.fp)]
        for p in self.patches:
            p.start()
        self.put(self.sys_data, "brave-browser", f"brave --x %U")

    async def asyncTearDown(self) -> None:
        for p in self.patches:
            p.stop()
        await super().asyncTearDown()

    def put(self, base: Path, app_id: str, exec_: str, extra: str = "") -> None:
        (base / "applications" / f"{app_id}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName={app_id}\nExec={exec_}\n{extra}")

    def default_browser(self, app_id: str, exit: int = 0) -> None:
        (self.fake / "xdg-mime_out").write_text(app_id + "\n" if app_id else "")
        (self.fake / "xdg-mime_exit").write_text(str(exit))

    async def test_open_app(self) -> None:
        res = await self.agent({"op": "open", "app": "brave-browser"})
        self.assertEqual(res, {"ok": True})
        [c] = self.calls("brave")
        self.assertEqual(c["argv"], ["--x"])
        self.assertTrue(c["new_session"])
        self.assertEqual(c["cwd"], "/")
        self.assertEqual(c["path"], str(self.sysbin))
        self.assertNotIn("LD_PRELOAD", c["env"])
        self.assertEqual(self.calls("gtk-launch"), [])
        self.assertEqual(self.calls("niri"), [])  # stage a: no permission, no focus check

    async def test_open_flatpak(self) -> None:
        self.put(self.fp, "com.spotify.Client", "/usr/bin/flatpak run com.spotify.Client @@u %U @@"
                 .replace("/usr/bin", str(self.sysbin)))
        self.assertEqual(await self.agent({"op": "open", "app": "com.spotify.Client"}), {"ok": True})
        [c] = self.calls("flatpak")
        self.assertEqual(c["argv"], ["run", "com.spotify.Client", "@@u", "@@"])

    async def test_open_refuses_home_and_terminals(self) -> None:
        self.put(self.home / ".local" / "share", "mine", "brave")
        self.put(self.sys_data, "evil", f"{self.home}/.local/bin/evil")
        self.put(self.sys_data, "wrapped", "sh -c brave")
        self.put(self.sys_data, "term", "brave", "Terminal=true\n")
        self.put(self.sys_data, "org.wezfurlong.wezterm", "brave")
        for app, needle in [("mine", "$HOME"), ("evil", "$HOME"), ("wrapped", "código"),
                            ("term", "terminal"), ("org.wezfurlong.wezterm", "terminal"),
                            ("nope", "No encontré")]:
            res = await self.agent({"op": "open", "app": app})
            self.assertFalse(res["ok"], app)
            self.assertIn(needle, res["error"], app)
        self.assertEqual(self.calls(), [])

    async def test_open_url(self) -> None:
        self.default_browser("brave-browser.desktop")
        res = await self.agent({"op": "open", "url": "https://open.spotify.com"})
        self.assertEqual(res, {"ok": True})
        [q] = self.calls("xdg-mime")
        self.assertEqual(q["argv"], ["query", "default", "x-scheme-handler/https"])
        [c] = self.calls("brave")
        self.assertEqual(c["argv"], ["--x", "https://open.spotify.com"])
        self.assertTrue(c["new_session"])
        self.assertEqual(self.calls("xdg-open"), [])

    async def test_open_url_refuses_bad_default(self) -> None:
        self.put(self.sys_data, "evil", f"{self.home}/.local/bin/evil %u")
        self.put(self.home / ".local" / "share", "mine", "brave %u")
        for default, exit, needle in [("evil.desktop", 0, "$HOME"), ("mine.desktop", 0, "$HOME"),
                                      ("", 0, "navegador por defecto"), ("brave-browser.desktop", 1,
                                                                         "navegador por defecto"),
                                      ("../x.desktop", 0, "navegador por defecto")]:
            self.default_browser(default, exit)
            res = await self.agent({"op": "open", "url": "https://x.org"})
            self.assertFalse(res["ok"], default)
            self.assertIn(needle, res["error"], default)
        self.assertEqual([c for c in self.calls() if c["name"] != "xdg-mime"], [])

    async def test_open_rejects(self) -> None:
        for req in [{"op": "open"}, {"op": "open", "app": "a", "url": "https://x"},
                    {"op": "open", "app": "-x"}, {"op": "open", "app": "../../bin/sh"},
                    {"op": "open", "app": 3}, {"op": "open", "url": "file:///etc/passwd"},
                    {"op": "open", "url": "javascript:alert(1)"}, {"op": "open", "url": "https://a b"}]:
            res = await self.agent(req)
            self.assertFalse(res["ok"], req)
        self.assertEqual(self.calls(), [])

    async def test_open_failure(self) -> None:
        (self.fake / "brave_exit").write_text("1")
        res = await self.agent({"op": "open", "app": "brave-browser"})
        self.assertFalse(res["ok"])
        self.assertIn("código 1", res["error"])


class PermissionTest(Base):
    async def test_denied_without_grant(self) -> None:
        for req in [{"op": "type", "text": "hola", "intent": "x"}, {"op": "key", "keys": "ctrl+l", "intent": "x"},
                    {"op": "screenshot"}]:
            res = await self.agent(req)
            self.assertFalse(res["ok"], req)
            self.assertIn("/control on", res["error"])
        self.assertEqual(self.calls(), [])

    async def test_revoke_denies(self) -> None:
        await self.grant()
        await self.control({"op": "revoke"})
        self.assertFalse((await self.agent({"op": "type", "text": "hola", "intent": "x"}))["ok"])
        self.assertEqual(self.calls("wtype"), [])


class TypeTest(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.grant()

    async def test_type(self) -> None:
        res = await self.agent({"op": "type", "text": "-hola mundo", "intent": "escribir nota"})
        self.assertEqual(res, {"ok": True})
        [c] = self.calls("wtype")
        # Every character is registered first (a release only), so wtype's keymap does not change
        # while typing; VS Code dropped keys after each change ("este es un texto" → "ee es uno", L27).
        reg = [x for cp in "-hola mund" for x in ("-p", hex(ord(cp)))]
        self.assertEqual(c["argv"], [*reg, "-k", "Shift_L", "-s", "250", "-d", "8", "--", "-hola mundo"])

    async def test_type_registers_each_character_once(self) -> None:
        res = await self.agent({"op": "type", "text": "añ\nañ€", "intent": "escribir nota"})
        self.assertEqual(res, {"ok": True})
        [c] = self.calls("wtype")
        euro = "0x20ac" if server._xkb_utf32_to_keysym else "0x10020ac"  # wtype types EuroSign
        self.assertEqual(c["argv"], ["-p", "0x61", "-p", "0xf1", "-p", "0xff0d", "-p", euro,
                                     "-k", "Shift_L", "-s", "250", "-d", "8", "--", "añ\nañ€"])
        niri = self.calls("niri")
        self.assertEqual(len(niri), 2)  # check + re-check right before wtype
        self.assertEqual(niri[0]["argv"], ["msg", "-j", "focused-window"])

    def test_keysyms_match_libxkbcommon(self) -> None:
        # wtype maps characters with xkb_utf32_to_keysym, which picks legacy keysyms for many
        # characters agents write (’ — … €); registering 0x1000000+cp left them mid-text (L28).
        if not server._xkb_utf32_to_keysym:
            self.skipTest("libxkbcommon not available")
        self.assertEqual(server.register_chars("’—…€"),
                         ["-p", "0xad1", "-p", "0xaa9", "-p", "0xaae", "-p", "0x20ac"])

    async def test_intent_optional(self) -> None:
        self.assertTrue((await self.agent({"op": "type", "text": "hola"}))["ok"])

    async def test_bad_text(self) -> None:
        for req in [{"op": "type"}, {"op": "type", "text": ""}, {"op": "type", "text": 3},
                    {"op": "type", "text": "a" * 2001}, {"op": "type", "text": "a\x1b[2J"},
                    {"op": "type", "text": "hola", "intent": 5}]:
            res = await self.agent(req)
            self.assertFalse(res["ok"], req)
        self.assertEqual(self.calls(), [])

    async def test_max_text(self) -> None:
        self.assertTrue((await self.agent({"op": "type", "text": "a" * 2000}))["ok"])

    async def test_focus_unreadable(self) -> None:
        for answer in [{"exit": 1}, {"stdout": "not json"}, {"stdout": "[]"}, {"stdout": "null"}]:
            self.focus(answer)
            res = await self.agent({"op": "type", "text": "hola"})
            self.assertFalse(res["ok"], answer)
        self.assertEqual(self.calls("wtype"), [])

    async def test_blocklist(self) -> None:
        for w in [{"id": 1, "app_id": "org.keepassxc.KeePassXC", "title": "db"},
                  {"id": 2, "app_id": "x", "title": "Authentication Required"},
                  {"id": 3, "app_id": "foot", "title": "jarvis"},
                  {"id": 4, "app_id": "gcr-prompter", "title": ""}]:
            self.focus(w)
            res = await self.agent({"op": "type", "text": "hola"})
            self.assertFalse(res["ok"], w)
            self.assertIn("bloqueo", res["error"])
            res = await self.agent({"op": "key", "keys": "ctrl+v"})
            self.assertFalse(res["ok"], w)
        self.assertEqual(self.calls("wtype"), [])

    async def test_focus_changed_before_wtype(self) -> None:
        self.focus(EDITOR, {"id": 99, "app_id": "org.gnome.TextEditor", "title": "Otra"})
        res = await self.agent({"op": "type", "text": "hola"})
        self.assertFalse(res["ok"])
        self.assertIn("foco", res["error"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_focus_unreadable_on_recheck(self) -> None:
        self.focus(EDITOR, {"exit": 1})
        self.assertFalse((await self.agent({"op": "type", "text": "hola"}))["ok"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_wtype_failure(self) -> None:
        (self.fake / "wtype_exit").write_text("1")
        self.assertFalse((await self.agent({"op": "type", "text": "hola"}))["ok"])

    async def test_logs_never_hold_text(self) -> None:
        secret = "contraseña-super-secreta-123"
        with self.assertLogs("jarvis_input", level="DEBUG") as logs:
            await self.agent({"op": "type", "text": secret, "intent": "nota"})
            await self.agent({"op": "type", "text": secret + "\x00"})
        joined = "\n".join(logs.output)
        self.assertNotIn(secret, joined)
        self.assertNotIn("super-secreta", joined)

    async def test_rate_limit(self) -> None:
        r, w = await self.connect(self.agent_path)
        results = [await self.ask_on(r, w, {"op": "type", "text": 3}) for _ in range(11)]
        self.assertIn("10 acciones por segundo", results[-1]["error"])
        self.assertTrue(all("acciones por segundo" not in x["error"] for x in results[:10]))


class KeyTest(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.grant()

    async def test_key(self) -> None:
        res = await self.agent({"op": "key", "keys": "ctrl+l", "intent": "barra"})
        self.assertEqual(res, {"ok": True})
        [c] = self.calls("wtype")
        # Keys: Escape is registered first (release only) so it keeps the real Escape keycode
        # VS Code matches shortcuts on (L24-L25); the Shift tap still carries the keymap.
        self.assertEqual(c["argv"], ["-p", "Escape", "-k", "Shift_L", "-s", "250",
                                     "-M", "ctrl", "-k", "l", "-m", "ctrl"])

    async def test_key_pauses_after_escape(self) -> None:
        # VS Code Vim needs a moment to leave insert mode before the next keys (L25).
        res = await self.agent({"op": "key", "keys": "Escape g g d shift+g", "intent": "modo vim"})
        self.assertTrue(res["ok"])
        [c] = self.calls("wtype")
        self.assertEqual(c["argv"], ["-p", "Escape", "-k", "Shift_L", "-s", "250", "-k", "Escape", "-s", "300",
                                     "-k", "g", "-k", "g", "-k", "d", "G"])

    async def test_no_pause_inside_modifier_or_at_end(self) -> None:
        pre = ["-p", "Escape", "-k", "Shift_L", "-s", "250"]
        for keys, tail in [
            ("ctrl+Escape g", ["-M", "ctrl", "-k", "Escape", "-m", "ctrl", "-k", "g"]),
            ("g Escape", ["-k", "g", "-k", "Escape"]),
            ("Escape Escape", ["-k", "Escape", "-s", "300", "-k", "Escape"]),
        ]:
            (self.fake / "calls.jsonl").unlink(missing_ok=True)
            self.assertTrue((await self.agent({"op": "key", "keys": keys, "intent": "modo vim"}))["ok"], keys)
            [c] = self.calls("wtype")
            self.assertEqual(c["argv"], pre + tail, keys)

    async def test_bad_keys(self) -> None:
        for req in [{"op": "key"}, {"op": "key", "keys": ""}, {"op": "key", "keys": "-P x"},
                    {"op": "key", "keys": ["a"]}, {"op": "key", "keys": "hyper+a"}]:
            self.assertFalse((await self.agent(req))["ok"], req)
        self.assertEqual(self.calls(), [])

    async def test_return_outside_messaging_needs_no_confirm(self) -> None:
        self.assertTrue((await self.agent({"op": "key", "keys": "Return"}))["ok"])


class ConfirmTest(Base):
    confirm_timeout = 0.6

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.grant()

    async def test_no_subscriber_denies_immediately(self) -> None:
        t = time.monotonic()
        res = await self.agent({"op": "type", "text": "enviar ahora"})
        self.assertFalse(res["ok"])
        self.assertLess(time.monotonic() - t, 0.5)
        self.assertEqual(self.calls("wtype"), [])

    async def _pending(self, req: dict[str, Any], subs: int = 1):
        readers = []
        for _ in range(subs):
            r, w = await self.subscribe()
            readers.append((r, w))
        task = asyncio.create_task(self.agent(req))
        events = [await self.next_event(r) for r, _ in readers]
        for e in events:
            self.assertEqual(e["event"], "confirm")
            self.assertEqual(set(e), {"event", "id", "title", "message", "timeout_s"})
            self.assertEqual(e["id"], events[0]["id"])
        return task, readers, events[0]

    async def test_allow(self) -> None:
        task, [(r, w)], ev = await self._pending({"op": "type", "text": "Envíe el pedido", "intent": "x"})
        self.assertEqual(ev["timeout_s"], 0.6)
        self.assertIn("Envíe el pedido", ev["message"])
        self.assertEqual(await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": True}), {"ok": True})
        self.assertEqual(await task, {"ok": True})
        self.assertEqual(len(self.calls("wtype")), 1)

    async def test_deny(self) -> None:
        task, [(r, w)], ev = await self._pending({"op": "key", "keys": "Return", "intent": "borrar fila"})
        await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": False})
        res = await task
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_timeout(self) -> None:
        task, _, _ = await self._pending({"op": "type", "text": "pay now"})
        res = await asyncio.wait_for(task, 3)
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_first_answer_wins(self) -> None:
        task, [(r1, w1), (r2, w2)], ev = await self._pending({"op": "type", "text": "comprar"}, subs=2)
        self.assertEqual(await self.ask_on(r1, w1, {"op": "answer", "id": ev["id"], "allow": True}), {"ok": True})
        late = await self.ask_on(r2, w2, {"op": "answer", "id": ev["id"], "allow": False})
        self.assertFalse(late["ok"])
        self.assertEqual(await task, {"ok": True})

    async def test_answer_validation(self) -> None:
        task, [(r, w)], ev = await self._pending({"op": "type", "text": "delete all"})
        for bad in [{"op": "answer", "id": ev["id"], "allow": "yes"}, {"op": "answer", "id": ev["id"]},
                    {"op": "answer", "id": "nope", "allow": True}, {"op": "answer", "allow": True}]:
            self.assertFalse((await self.ask_on(r, w, bad))["ok"], bad)
        # answers on a separate control connection count too
        self.assertEqual(await self.control({"op": "answer", "id": ev["id"], "allow": False}), {"ok": True})
        self.assertFalse((await task)["ok"])

    async def test_subscribers_gone_denies(self) -> None:
        task, [(r, w)], _ = await self._pending({"op": "type", "text": "send it"})
        w.close()
        t = time.monotonic()
        self.assertFalse((await asyncio.wait_for(task, 3))["ok"])
        self.assertLess(time.monotonic() - t, 0.5)

    async def test_revoke_while_pending_denies(self) -> None:
        task, [(r, w)], ev = await self._pending({"op": "type", "text": "send it"})
        await self.control({"op": "revoke"})
        self.assertFalse((await task)["ok"])
        # even a late allow cannot type
        await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": True})
        self.assertEqual(self.calls("wtype"), [])

    async def test_focus_recheck_after_confirm(self) -> None:
        self.focus(EDITOR, {"id": 50, "app_id": "org.keepassxc.KeePassXC", "title": "db"})
        task, [(r, w)], ev = await self._pending({"op": "type", "text": "send it"})
        await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": True})
        self.assertFalse((await task)["ok"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_risky_variants_confirm(self) -> None:
        for text in ["BORRAR todo", "e\u0301nviar", "\uff53\uff45\uff4e\uff44", "se\u200bnd", "Submit", "eliminados"]:
            task, [(r, w)], ev = await self._pending({"op": "type", "text": text})
            await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": False})
            self.assertFalse((await task)["ok"], text)
            w.close()
        self.assertEqual(self.calls("wtype"), [])

    async def test_intent_risky_confirms(self) -> None:
        task, [(r, w)], ev = await self._pending({"op": "key", "keys": "ctrl+v", "intent": "pagar factura"})
        await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": True})
        self.assertTrue((await task)["ok"])

    async def test_lookalike_denied_outright(self) -> None:
        await self.subscribe()
        res = await self.agent({"op": "type", "text": "s\u0435nd"})
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_return_in_messaging(self) -> None:
        self.focus(BRAVE)
        for req in [{"op": "key", "keys": "Return"}, {"op": "key", "keys": "ctrl+Return"},
                    {"op": "type", "text": "hola\n"}]:
            task, [(r, w)], ev = await self._pending(req)
            self.assertIn("brave-browser", ev["message"])
            await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": False})
            self.assertFalse((await task)["ok"], req)
            w.close()
        self.focus({"id": 9, "app_id": "org.mozilla.Thunderbird", "title": "Redactar"})
        self.assertFalse((await self.agent({"op": "key", "keys": "ctrl+Return"}))["ok"])  # no subscriber
        self.assertEqual(self.calls("wtype"), [])
        self.assertTrue((await self.agent({"op": "type", "text": "hola"}))["ok"])  # no Return: fine


class RevokeKillsTest(Base):
    async def test_revoke_kills_wtype(self) -> None:
        await self.grant()
        (self.fake / "wtype_sleep").write_text("20")
        task = asyncio.create_task(self.agent({"op": "type", "text": "hola"}, timeout=10))
        for _ in range(100):
            if self.calls("wtype"):
                break
            await asyncio.sleep(0.05)
        [c] = self.calls("wtype")
        t = time.monotonic()
        await self.control({"op": "revoke"})
        res = await asyncio.wait_for(task, 3)
        self.assertFalse(res["ok"])
        self.assertLess(time.monotonic() - t, 2)
        await asyncio.sleep(0.1)
        with self.assertRaises(ProcessLookupError):
            os.kill(c["pid"], 0)


class ScreenshotTest(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.grant()

    async def test_screenshot(self) -> None:
        res = await self.agent({"op": "screenshot"})
        self.assertEqual(set(res), {"ok", "png_base64"})
        self.assertEqual(base64.b64decode(res["png_base64"]), PNG)
        self.assertEqual(self.calls("grim")[0]["argv"], ["-"])

    async def test_scale(self) -> None:
        res = await self.agent({"op": "screenshot", "scale": 0.5})
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls("grim")[0]["argv"], ["-s", "0.5", "-"])

    async def test_bad_scale(self) -> None:
        for bad in [0, -1, 3, "0.5", True, float("inf")]:
            self.assertFalse((await self.agent({"op": "screenshot", "scale": bad}))["ok"], bad)
        self.assertEqual(self.calls("grim"), [])

    async def test_blocked_focus(self) -> None:
        self.focus({"id": 1, "app_id": "Bitwarden", "title": "Vault"})
        res = await self.agent({"op": "screenshot"})
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls("grim"), [])
        self.focus({"exit": 1})
        self.assertFalse((await self.agent({"op": "screenshot"}))["ok"])
        self.assertEqual(self.calls("grim"), [])

    async def test_terminal_and_empty_focus_allowed(self) -> None:
        self.focus({"id": 3, "app_id": "foot", "title": "jarvis"})
        self.assertTrue((await self.agent({"op": "screenshot"}))["ok"])
        self.focus(None)
        self.assertTrue((await self.agent({"op": "screenshot"}))["ok"])

    async def test_grim_failure(self) -> None:
        (self.fake / "grim_exit").write_text("1")
        self.assertFalse((await self.agent({"op": "screenshot"}))["ok"])

    async def test_logs_never_hold_image(self) -> None:
        with self.assertLogs("jarvis_input", level="DEBUG") as logs:
            res = await self.agent({"op": "screenshot"})
        self.assertNotIn(res["png_base64"], "\n".join(logs.output))


GHOSTTY = {"id": 3, "app_id": "com.mitchellh.ghostty", "title": "jarvis"}
KEEPASS = {"id": 5, "app_id": "org.keepassxc.KeePassXC", "title": "db.kdbx"}
NOTEPAD = {"id": 11, "app_id": "dev.duxlabs.shell.notepad", "title": "Sin título"}


class AppsTest(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        root = Path(self.tmp.name)
        self.home_data, self.sys_data = root / "data-home", root / "data-sys"
        for base, rel, text in [
            (self.sys_data, "dev.duxlabs.shell.notepad.desktop",
             "[Desktop Entry]\nType=Application\nName=Notepad\nName[es]=Bloc de notas\nExec=x\n"),
            (self.sys_data, "brave-browser.desktop", "[Desktop Entry]\nType=Application\nName=Brave\nExec=x\n"),
            (self.home_data, "brave-browser.desktop", "[Desktop Entry]\nType=Application\nName=Brave mío\nExec=x\n"),
            (self.sys_data, "secret.desktop", "[Desktop Entry]\nType=Application\nName=S\nNoDisplay=true\n"),
        ]:
            p = base / "applications" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        os.environ["XDG_DATA_HOME"] = str(self.home_data)
        os.environ["XDG_DATA_DIRS"] = str(self.sys_data)
        self.flatpak = mock.patch.object(server.checks, "FLATPAK_SYSTEM_EXPORTS", root / "no-flatpak")
        self.flatpak.start()

    async def asyncTearDown(self) -> None:
        self.flatpak.stop()
        await super().asyncTearDown()

    async def test_apps_without_permission(self) -> None:
        res = await self.agent({"op": "apps"})
        self.assertEqual(res, {"ok": True, "apps": [
            {"id": "dev.duxlabs.shell.notepad", "name": "Bloc de notas"},
            {"id": "brave-browser", "name": "Brave mío"},
        ]})
        self.assertEqual(self.calls(), [])  # nothing executed

    async def test_apps_query(self) -> None:
        res = await self.agent({"op": "apps", "query": "NÓTAS"})
        self.assertEqual(res["apps"], [{"id": "dev.duxlabs.shell.notepad", "name": "Bloc de notas"}])
        for bad in [3, None, "x" * 201]:
            self.assertFalse((await self.agent({"op": "apps", "query": bad}))["ok"], bad)


class WindowsTest(Base):
    async def test_requires_permission(self) -> None:
        self.windows(EDITOR)
        for req in [{"op": "windows"}, {"op": "focus", "window_id": 7}]:
            res = await self.agent(req)
            self.assertFalse(res["ok"], req)
            self.assertIn("/control on", res["error"])
        self.assertEqual(self.calls(), [])

    async def test_list(self) -> None:
        await self.grant()
        self.windows({**GHOSTTY, "focused": True}, {**NOTEPAD, "title": "t" * 300})
        res = await self.agent({"op": "windows"})
        self.assertEqual(res, {"ok": True, "windows": [
            {"id": 3, "app_id": "com.mitchellh.ghostty", "title": "jarvis", "focused": True},
            {"id": 11, "app_id": "dev.duxlabs.shell.notepad", "title": "t" * 200, "focused": False},
        ]})
        [c] = self.calls("niri")
        self.assertEqual(c["argv"], ["msg", "-j", "windows"])

    async def test_unreadable(self) -> None:
        await self.grant()
        for kwargs in [{"exit": 1}, {"raw": "nope"}, {"raw": "{}"}]:
            self.windows(EDITOR, **kwargs)  # type: ignore[arg-type]
            res = await self.agent({"op": "windows"})
            self.assertFalse(res["ok"], kwargs)


class FocusTest(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.grant()
        self.windows({**GHOSTTY, "focused": True}, NOTEPAD, KEEPASS, BRAVE)

    def actions(self) -> list[list[str]]:
        return [c["argv"] for c in self.calls("niri") if "action" in c["argv"]]

    async def test_focus(self) -> None:
        self.focus(GHOSTTY, GHOSTTY, NOTEPAD)  # focus changes on the third poll
        res = await self.agent({"op": "focus", "window_id": 11})
        self.assertEqual(res, {"ok": True})
        self.assertEqual(self.actions(), [["msg", "action", "focus-window", "--id", "11"]])

    async def test_focus_not_confirmed(self) -> None:
        self.focus(GHOSTTY)
        start = time.monotonic()
        res = await self.agent({"op": "focus", "window_id": 11})
        self.assertFalse(res["ok"])
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual(len(self.actions()), 1)

    async def test_focus_unreadable_after(self) -> None:
        self.focus({"exit": 1})
        self.assertFalse((await self.agent({"op": "focus", "window_id": 11}))["ok"])

    async def test_refuses_blocked_terminal_missing_and_bad_ids(self) -> None:
        for wid in [3, 5, 99, "11", True, None, 1.5, -1]:
            res = await self.agent({"op": "focus", "window_id": wid})
            self.assertFalse(res["ok"], wid)
        res = await self.agent({"op": "focus", "window_id": 3})
        self.assertIn("terminal", res["error"])
        res = await self.agent({"op": "focus", "window_id": 5})
        self.assertIn("bloqueo", res["error"])
        self.assertEqual(self.actions(), [])

    async def test_windows_unreadable(self) -> None:
        self.windows(raw="nope")
        self.assertFalse((await self.agent({"op": "focus", "window_id": 11}))["ok"])
        self.assertEqual(self.actions(), [])

    async def test_action_failure(self) -> None:
        (self.fake / "niri_action_exit").write_text("1")
        self.focus(NOTEPAD)
        self.assertFalse((await self.agent({"op": "focus", "window_id": 11}))["ok"])

    async def test_rate_limit(self) -> None:
        self.helper._limiter = server.checks.RateLimiter(server.RATE_MAX, server.RATE_WINDOW_S, clock=lambda: 0.0)
        r, w = await self.connect(self.agent_path)
        results = [await self.ask_on(r, w, {"op": "windows"}) for _ in range(server.RATE_MAX + 2)]
        self.assertTrue(all(x["ok"] for x in results[: server.RATE_MAX]))
        self.assertEqual(results[-1], {"ok": False, "error": server.RATE})
        res = await self.ask_on(r, w, {"op": "focus", "window_id": 11})
        self.assertEqual(res, {"ok": False, "error": server.RATE})

    async def test_control_socket_does_not_know_them(self) -> None:
        for op in ["apps", "windows", "focus"]:
            self.assertFalse((await self.control({"op": op, "window_id": 11}))["ok"], op)
        self.assertEqual(self.calls(), [])


class TypeIntoWindowTest(Base):
    """S16: type/key with window_id focus the target, type, and give focus back."""
    confirm_timeout = 0.6

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.grant()
        self.windows({**GHOSTTY, "focused": True}, NOTEPAD, KEEPASS, BRAVE)

    def actions(self) -> list[list[str]]:
        return [c["argv"] for c in self.calls("niri") if "action" in c["argv"]]

    def order(self) -> list[str]:
        return ["wtype" if c["name"] == "wtype" else "focus " + c["argv"][-1]
                for c in self.calls() if c["name"] == "wtype" or "action" in c["argv"]]

    async def test_type_focuses_types_and_restores(self) -> None:
        self.focus(GHOSTTY, NOTEPAD)  # user on the TUI; the target is focused after the action
        res = await self.agent({"op": "type", "text": "hola", "window_id": 11})
        self.assertEqual(res, {"ok": True, "window_id": 11})
        self.assertEqual(self.order(), ["focus 11", "wtype", "focus 3"])
        [c] = self.calls("wtype")
        self.assertEqual(c["argv"], ["-p", "0x68", "-p", "0x6f", "-p", "0x6c", "-p", "0x61",
                                     "-k", "Shift_L", "-s", "250", "-d", "8", "--", "hola"])

    async def test_key_focuses_types_and_restores(self) -> None:
        self.focus(GHOSTTY, NOTEPAD)
        res = await self.agent({"op": "key", "keys": "ctrl+n", "window_id": 11})
        self.assertEqual(res, {"ok": True, "window_id": 11})
        self.assertEqual(self.order(), ["focus 11", "wtype", "focus 3"])

    async def _sleeps_during(self, req: dict) -> tuple[dict, list[float]]:
        real, slept = asyncio.sleep, []

        async def spy(delay: float, *a: object, **k: object) -> object:
            slept.append(delay)
            return await real(delay, *a, **k)

        with mock.patch.object(server.asyncio, "sleep", spy):
            res = await self.agent(req)
        return res, slept

    async def test_settles_after_focus_change_before_typing(self) -> None:
        # "la mundo" (L21): VS Code drops the first keys right after it gets focus back.
        self.focus(GHOSTTY, NOTEPAD)
        res, slept = await self._sleeps_during({"op": "type", "text": "hola", "window_id": 11})
        self.assertEqual(res, {"ok": True, "window_id": 11})
        self.assertIn(server.FOCUS_SETTLE_S, slept)
        self.assertGreaterEqual(server.FOCUS_SETTLE_S, 0.3)
        self.assertEqual(self.order(), ["focus 11", "wtype", "focus 3"])

    async def test_no_settle_when_target_already_focused(self) -> None:
        self.focus(NOTEPAD, NOTEPAD, NOTEPAD)
        res, slept = await self._sleeps_during({"op": "type", "text": "hola", "window_id": 11})
        self.assertEqual(res, {"ok": True, "window_id": 11})
        self.assertNotIn(server.FOCUS_SETTLE_S, slept)
        self.assertEqual(self.order(), ["wtype"])  # no focus changes, nothing to give back

    async def test_focus_confirm_polls(self) -> None:
        self.focus(GHOSTTY, GHOSTTY, GHOSTTY, NOTEPAD)  # focus lands on the 2nd poll
        self.assertTrue((await self.agent({"op": "type", "text": "hola", "window_id": 11}))["ok"])

    async def test_refuses_bad_targets(self) -> None:
        self.focus(GHOSTTY, NOTEPAD)
        self.helper._limiter = server.checks.RateLimiter(100, server.RATE_WINDOW_S)
        r, w = await self.connect(self.agent_path)
        for wid in [3, 5, 99, "11", True, None, 1.5, -1]:
            res = await self.ask_on(r, w, {"op": "type", "text": "hola", "window_id": wid})
            self.assertFalse(res["ok"], wid)
            self.assertFalse((await self.ask_on(r, w, {"op": "key", "keys": "a", "window_id": wid}))["ok"], wid)
        res = await self.agent({"op": "type", "text": "hola", "window_id": 3})
        self.assertIn("terminal", res["error"])
        res = await self.agent({"op": "type", "text": "hola", "window_id": 5})
        self.assertIn("bloqueo", res["error"])
        self.assertEqual(self.order(), [])

    async def test_focus_not_confirmed(self) -> None:
        self.focus(GHOSTTY)
        res = await self.agent({"op": "type", "text": "hola", "window_id": 11})
        self.assertFalse(res["ok"])
        self.assertIn("enfocada", res["error"])
        self.assertEqual(self.order(), ["focus 11", "focus 3"])  # focus goes back even so

    async def test_without_window_id_unchanged(self) -> None:
        self.focus(NOTEPAD)
        self.assertEqual(await self.agent({"op": "type", "text": "hola"}), {"ok": True})
        self.assertEqual(await self.agent({"op": "key", "keys": "ctrl+n"}), {"ok": True})
        self.assertEqual(self.order(), ["wtype", "wtype"])  # no focus changes
        self.focus(GHOSTTY)
        res = await self.agent({"op": "type", "text": "hola"})
        self.assertIn("terminal", res["error"])
        self.assertEqual(self.order(), ["wtype", "wtype"])

    async def test_recheck_compares_target(self) -> None:
        self.focus(GHOSTTY, NOTEPAD, GHOSTTY)  # confirmed, then the user switched before wtype
        res = await self.agent({"op": "type", "text": "hola", "window_id": 11})
        self.assertFalse(res["ok"])
        self.assertIn("foco", res["error"])
        self.assertEqual(self.calls("wtype"), [])

    async def test_no_restore_when_previous_is_target_or_gone(self) -> None:
        self.focus(NOTEPAD)
        self.assertTrue((await self.agent({"op": "type", "text": "hola", "window_id": 11}))["ok"])
        self.assertEqual(self.order(), ["wtype"])  # already focused: no focus change at all
        (self.fake / "calls.jsonl").unlink()
        self.focus({"id": 42, "app_id": "x", "title": "cerrada"}, NOTEPAD)
        self.assertTrue((await self.agent({"op": "type", "text": "hola", "window_id": 11}))["ok"])
        self.assertEqual(self.order(), ["focus 11", "wtype"])

    async def test_no_previous_window(self) -> None:
        self.focus({"stdout": "null"}, NOTEPAD)
        self.assertTrue((await self.agent({"op": "type", "text": "hola", "window_id": 11}))["ok"])
        self.assertEqual(self.order(), ["focus 11", "wtype"])

    async def test_restore_failure_is_not_an_error(self) -> None:
        (self.fake / "niri_action_exit_3").write_text("1")
        self.focus(GHOSTTY, NOTEPAD)
        with self.assertLogs("jarvis_input", level="WARNING"):
            res = await self.agent({"op": "type", "text": "hola", "window_id": 11})
        self.assertEqual(res, {"ok": True, "window_id": 11})
        self.assertEqual(self.order(), ["focus 11", "wtype", "focus 3"])

    async def test_restores_after_wtype_failure(self) -> None:
        (self.fake / "wtype_exit").write_text("1")
        self.focus(GHOSTTY, NOTEPAD)
        self.assertFalse((await self.agent({"op": "type", "text": "hola", "window_id": 11}))["ok"])
        self.assertEqual(self.order(), ["focus 11", "wtype", "focus 3"])

    async def test_confirmation_names_target_before_focusing(self) -> None:
        self.focus(NOTEPAD)  # the user is elsewhere; the target is Brave
        r, w = await self.subscribe()
        task = asyncio.create_task(self.agent({"op": "key", "keys": "Return", "window_id": 8}))
        ev = await self.next_event(r)
        self.assertEqual(ev["event"], "confirm")
        self.assertIn("brave-browser", ev["message"])
        self.assertIn("Inbox", ev["message"])
        self.assertEqual(self.actions(), [])  # nothing focused while asking
        await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": False})
        self.assertFalse((await task)["ok"])
        self.assertEqual(self.order(), [])

    async def test_confirm_allowed_then_typed(self) -> None:
        self.focus(GHOSTTY, BRAVE)
        r, w = await self.subscribe()
        task = asyncio.create_task(self.agent({"op": "type", "text": "hola\n", "window_id": 8}))
        ev = await self.next_event(r)
        await self.ask_on(r, w, {"op": "answer", "id": ev["id"], "allow": True})
        self.assertEqual(await task, {"ok": True, "window_id": 8})
        self.assertEqual(self.order(), ["focus 8", "wtype", "focus 3"])

    async def test_return_rule_uses_target_not_focused(self) -> None:
        self.focus(BRAVE, NOTEPAD)  # focused Brave would need a confirmation; the target does not
        self.assertTrue((await self.agent({"op": "key", "keys": "Return", "window_id": 11}))["ok"])

    async def test_requires_permission(self) -> None:
        await self.control({"op": "revoke"})
        res = await self.agent({"op": "type", "text": "hola", "window_id": 11})
        self.assertIn("/control on", res["error"])
        self.assertEqual(self.calls(), [])


class ScreenshotWindowTest(Base):
    """S17: screenshot with window_id focuses the target, captures its output, gives focus back."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.grant()
        self.windows({**GHOSTTY, "focused": True}, NOTEPAD, KEEPASS, BRAVE)
        self.output("HDMI-A-1")

    def output(self, name: str | None = None, *, exit: int = 0, raw: str | None = None) -> None:
        out = raw if raw is not None else json.dumps(None if name is None else {"name": name, "logical": {}})
        (self.fake / "output.json").write_text(json.dumps({"stdout": out, "exit": exit}))

    def order(self) -> list[str]:
        return ["grim" if c["name"] == "grim" else "focus " + c["argv"][-1]
                for c in self.calls() if c["name"] == "grim" or "action" in c["argv"]]

    async def test_focuses_captures_output_and_restores(self) -> None:
        self.focus(GHOSTTY, NOTEPAD)  # user on the TUI; the target is focused after the action
        res = await self.agent({"op": "screenshot", "scale": 0.5, "window_id": 11})
        self.assertEqual(set(res), {"ok", "png_base64", "window_id"})
        self.assertEqual(res["window_id"], 11)
        self.assertEqual(base64.b64decode(res["png_base64"]), PNG)
        self.assertEqual(self.order(), ["focus 11", "grim", "focus 3"])
        [g] = self.calls("grim")
        self.assertEqual(g["argv"], ["-s", "0.5", "-o", "HDMI-A-1", "-"])

    async def test_settles_before_capture(self) -> None:
        real, slept = asyncio.sleep, []

        async def spy(delay: float, *a: object, **k: object) -> object:
            slept.append(delay)
            return await real(delay, *a, **k)

        self.focus(GHOSTTY, NOTEPAD)
        with mock.patch.object(server.asyncio, "sleep", spy):
            self.assertTrue((await self.agent({"op": "screenshot", "window_id": 11}))["ok"])
        self.assertIn(server.FOCUS_SETTLE_S, slept)

    async def test_terminal_target_allowed(self) -> None:
        # Screenshots skip the terminal rule (the user may want Jarvis to read the TUI).
        self.focus(NOTEPAD, GHOSTTY)
        res = await self.agent({"op": "screenshot", "window_id": 3})
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.order(), ["focus 3", "grim", "focus 11"])

    async def test_refuses_blocked_missing_and_bad_ids(self) -> None:
        self.focus(GHOSTTY, NOTEPAD)
        self.helper._limiter = server.checks.RateLimiter(100, server.RATE_WINDOW_S)
        r, w = await self.connect(self.agent_path)
        for wid in [5, 99, "11", True, None, 1.5, -1]:
            res = await self.ask_on(r, w, {"op": "screenshot", "window_id": wid})
            self.assertFalse(res["ok"], wid)
        res = await self.ask_on(r, w, {"op": "screenshot", "window_id": 5})
        self.assertIn("bloqueo", res["error"])
        self.assertEqual(self.order(), [])

    async def test_already_focused_no_focus_change(self) -> None:
        self.focus(NOTEPAD)
        res = await self.agent({"op": "screenshot", "window_id": 11})
        self.assertEqual(res["window_id"], 11)
        self.assertEqual(self.order(), ["grim"])

    async def test_focus_not_confirmed(self) -> None:
        self.focus(GHOSTTY)
        res = await self.agent({"op": "screenshot", "window_id": 11})
        self.assertFalse(res["ok"])
        self.assertIn("enfocada", res["error"])
        self.assertEqual(self.order(), ["focus 11", "focus 3"])  # no capture; focus goes back

    async def test_recheck_right_before_capture(self) -> None:
        self.focus(GHOSTTY, NOTEPAD, GHOSTTY)  # confirmed, then the user switched before grim
        res = await self.agent({"op": "screenshot", "window_id": 11})
        self.assertFalse(res["ok"])
        self.assertIn("foco", res["error"])
        self.assertEqual(self.calls("grim"), [])
        self.assertEqual(self.order(), ["focus 11", "focus 3"])

    async def test_unreadable_output_captures_everything(self) -> None:
        for kwargs in [{"exit": 1}, {"raw": "nope"}, {"raw": "null"}, {"raw": '{"name": "bad name;"}'},
                       {"raw": '{"name": 3}'}]:
            (self.fake / "calls.jsonl").unlink(missing_ok=True)
            self.helper._limiter = server.checks.RateLimiter(100, server.RATE_WINDOW_S)
            self.output(**kwargs)  # type: ignore[arg-type]
            self.focus(NOTEPAD)
            res = await self.agent({"op": "screenshot", "window_id": 11})
            self.assertTrue(res["ok"], kwargs)
            self.assertEqual(self.calls("grim")[0]["argv"], ["-"], kwargs)

    async def test_restores_after_grim_failure(self) -> None:
        (self.fake / "grim_exit").write_text("1")
        self.focus(GHOSTTY, NOTEPAD)
        self.assertFalse((await self.agent({"op": "screenshot", "window_id": 11}))["ok"])
        self.assertEqual(self.order(), ["focus 11", "grim", "focus 3"])

    async def test_without_window_id_unchanged(self) -> None:
        self.focus(GHOSTTY)
        res = await self.agent({"op": "screenshot"})
        self.assertEqual(set(res), {"ok", "png_base64"})
        self.assertEqual(self.order(), ["grim"])
        self.assertEqual(self.calls("grim")[0]["argv"], ["-"])
        self.assertFalse(any("focused-output" in c["argv"] for c in self.calls("niri")))

    async def test_requires_permission(self) -> None:
        await self.control({"op": "revoke"})
        res = await self.agent({"op": "screenshot", "window_id": 11})
        self.assertIn("/control on", res["error"])
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
