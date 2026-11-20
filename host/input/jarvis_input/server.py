"""The jarvis-input sockets (S13): agent.sock for the agent, control.sock for the TUI and jarvis-stop.

Newline-delimited JSON, one request -> one response. The permission lives only in this process's
memory: restarting the helper revokes it. Every argv is built here and run without a shell.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import math
import os
import secrets
import signal
import socket
import stat
import struct
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import checks, launch

log = logging.getLogger("jarvis_input")

MAX_REQUEST = 64 * 1024
MAX_GRANT_MINUTES = 30
CONFIRM_TIMEOUT_S = 30
IDLE_TIMEOUT_S = 30.0
MAX_CONNECTIONS = 16
MAX_PNG_BYTES = 64 * 1024 * 1024
RATE_MAX, RATE_WINDOW_S = 10, 1.0
NIRI_TIMEOUT_S = 3.0
WTYPE_TIMEOUT_S = 60.0
GRIM_TIMEOUT_S = 15.0
OPEN_WAIT_S = 5.0
FOCUS_CONFIRM_S, FOCUS_POLL_S = 0.5, 0.05
RESTORE_DELAY_S = 0.1  # let the target app take the last keys before focus goes back (S16)
# After focus moves to another window, Electron apps (VS Code) need a moment to give the
# editor its focus back; keys sent sooner are lost ("hola mundo" typed "la mundo", L21).
FOCUS_SETTLE_S = 0.4
SUBSCRIBER_BUFFER_MAX = 1024 * 1024

OFF = "Control de la PC apagado. Pídele al usuario que ejecute /control on en la TUI de Jarvis."
RATE = f"Límite de {RATE_MAX} acciones por segundo superado; espera un momento."


class StartupError(Exception):
    """The helper refuses to start (unsafe or unusable runtime directory or socket)."""


# wtype uploads a fresh keymap on every run; Chromium/Electron apps (Brave, VS Code) drop
# the first keystrokes that arrive before they apply it ("wtype hola" typed only "la" in
# VS Code, L19). A sleep alone did not help ("la mundo"): wtype sends the keymap with the
# first key. So the first key is a harmless Shift tap that carries the keymap, then a sleep
# while the app applies it, then the real keys with a small delay between them.
WTYPE_PRELUDE = ("-k", "Shift_L", "-s", "250")
# wtype numbers keysyms in order of first use and VS Code matches shortcuts on the keycode, so
# for keys Escape is registered first (a release only, which apps ignore) to keep the real
# Escape keycode; after the Shift tap it would land on "1" and Vim never saw it (L24-L25).
# Text keeps the plain prelude: with the Escape release first VS Code dropped "ho" again (L25).
WTYPE_KEY_PRELUDE = ("-p", "Escape", *WTYPE_PRELUDE)
ESCAPE_PAUSE_MS = "300"  # VS Code Vim needs a moment to leave insert mode (L25)


def _load_xkb_utf32_to_keysym():  # type: ignore[no-untyped-def]
    """libxkbcommon's xkb_utf32_to_keysym, the mapping wtype uses, or None when unavailable."""
    import ctypes
    import ctypes.util

    name = ctypes.util.find_library("xkbcommon")
    try:
        lib = ctypes.CDLL(name or "libxkbcommon.so.0")
        fn = lib.xkb_utf32_to_keysym
    except (OSError, AttributeError):
        return None
    fn.argtypes, fn.restype = [ctypes.c_uint32], ctypes.c_uint32
    return fn


_xkb_utf32_to_keysym = _load_xkb_utf32_to_keysym()


def _keysym(ch: str) -> int:
    """The keysym wtype types for one character: its own remaps, then xkb_utf32_to_keysym
    (legacy keysyms for ’ — … € and many more, L28); without libxkbcommon, the Unicode keysym."""
    cp = ord(ch)
    if ch == "\n":
        return 0xFF0D  # Return
    if ch == "\t":
        return 0xFF09  # Tab
    if _xkb_utf32_to_keysym is not None:
        ks = _xkb_utf32_to_keysym(cp)
        if ks:
            return int(ks)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp  # Latin-1 keysyms equal the code point
    return 0x1000000 + cp


def register_chars(text: str) -> list[str]:
    """wtype adds each new keysym to its keymap and uploads it again while typing; VS Code drops
    the keys around every upload ("este es un texto" typed "ee es uno", L27). Releasing every
    character once up front (apps act on press) builds the whole keymap before the Shift tap."""
    seen: dict[int, None] = {}
    for ch in text:
        seen.setdefault(_keysym(ch), None)
    return [x for ks in seen for x in ("-p", hex(ks))]
WTYPE_KEY_DELAY_MS = "8"


class Deny(Exception):
    """An action is refused; the message goes back to the agent as the error."""


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def runtime_dir_from_env(env: dict[str, str] | os._Environ[str]) -> Path:
    value = env.get("XDG_RUNTIME_DIR", "")
    if not value or not os.path.isabs(value):
        raise StartupError("XDG_RUNTIME_DIR no está definido o no es una ruta absoluta")
    try:
        st = os.stat(value)
    except OSError as e:
        raise StartupError(f"XDG_RUNTIME_DIR inaccesible: {e}") from None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise StartupError("XDG_RUNTIME_DIR no es un directorio de este usuario")
    return Path(value)


def prepare_dir(path: Path) -> None:
    """Create `path` 0700, or accept an existing real directory of ours (fixing its mode). No symlinks."""
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o700)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        raise StartupError(f"{path}: no es un directorio real (¿enlace simbólico?): {e}") from None
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            raise StartupError(f"{path}: no es de este usuario")
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def clear_stale(path: Path) -> None:
    """Remove a leftover socket nobody listens on. Refuse a live socket or any non-socket file."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise StartupError(f"{path}: existe y no es un socket; no lo toco")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        return
    except OSError as e:
        raise StartupError(f"{path}: no pude comprobar el socket: {e}") from None
    finally:
        probe.close()
    raise StartupError(f"{path}: otro jarvis-input ya está escuchando")


def _peer_uid(writer: asyncio.StreamWriter) -> int | None:
    sock = writer.get_extra_info("socket")
    if sock is None:
        return None
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return None
    return struct.unpack("3i", creds)[1]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constante JSON no permitida: {name}")


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


class Helper:
    def __init__(self, runtime_dir: Path, *, confirm_timeout: float = CONFIRM_TIMEOUT_S,
                 idle_timeout: float = IDLE_TIMEOUT_S) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.agent_path = self.runtime_dir / "jarvis" / "agent.sock"
        self.control_path = self.runtime_dir / "jarvis-control" / "control.sock"
        self.confirm_timeout = confirm_timeout
        self.idle_timeout = idle_timeout
        self._servers: list[asyncio.Server] = []
        self._inodes: dict[Path, int] = {}
        self._conns: dict[str, set[asyncio.StreamWriter]] = {"agent": set(), "control": set()}
        self._subscribers: set[asyncio.StreamWriter] = set()
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._children: set[asyncio.subprocess.Process] = set()
        self._killed: set[asyncio.subprocess.Process] = set()
        self._background: set[asyncio.Task[Any]] = set()
        self._action_lock = asyncio.Lock()
        self._limiter = checks.RateLimiter(RATE_MAX, RATE_WINDOW_S)
        self._expires_mono: float | None = None
        self._expires_at: str | None = None
        self._timer: asyncio.TimerHandle | None = None

    # --- lifecycle -----------------------------------------------------------------------

    async def start(self) -> None:
        for path in (self.agent_path, self.control_path):
            prepare_dir(path.parent)
        for path in (self.agent_path, self.control_path):
            clear_stale(path)
        for path, kind in ((self.agent_path, "agent"), (self.control_path, "control")):
            old = os.umask(0o177)
            try:
                srv = await asyncio.start_unix_server(
                    lambda r, w, k=kind: self._serve(r, w, k), path=str(path), limit=MAX_REQUEST)
            finally:
                os.umask(old)
            os.chmod(path, 0o600)
            self._inodes[path] = os.lstat(path).st_ino
            self._servers.append(srv)
        log.info("escuchando en %s y %s", self.agent_path, self.control_path)

    async def close(self) -> None:
        self._clear_permission()
        for srv in self._servers:
            srv.close()
        for conns in self._conns.values():
            for w in list(conns):
                w.close()
        for srv in self._servers:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(srv.wait_closed(), 2)
        for path, ino in self._inodes.items():
            with contextlib.suppress(OSError):
                if os.lstat(path).st_ino == ino:
                    os.unlink(path)
        for task in list(self._background):
            task.cancel()
        self._servers.clear()

    # --- permission ----------------------------------------------------------------------

    def _enabled(self) -> bool:
        if self._expires_mono is None:
            return False
        if time.monotonic() >= self._expires_mono:
            log.info("permiso vencido")
            self._clear_permission()
            self._push_state()
            return False
        return True

    def _state(self) -> dict[str, Any]:
        enabled = self._enabled()
        return {"enabled": enabled, "expires_at": self._expires_at if enabled else None}

    def _clear_permission(self) -> None:
        self._expires_mono = None
        self._expires_at = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for proc in list(self._children):
            self._killed.add(proc)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(False)

    def _on_expiry(self) -> None:
        self._timer = None
        self._enabled()

    def _grant(self, minutes: float) -> str:
        seconds = minutes * 60
        self._expires_mono = time.monotonic() + seconds
        self._expires_at = iso(time.time() + seconds)
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(seconds + 0.01, self._on_expiry)
        return self._expires_at

    # --- subscribers ---------------------------------------------------------------------

    def _broadcast(self, event: dict[str, Any]) -> None:
        line = (json.dumps(event) + "\n").encode()
        for w in list(self._subscribers):
            transport = w.transport
            if w.is_closing() or transport.get_write_buffer_size() > SUBSCRIBER_BUFFER_MAX:
                self._drop_subscriber(w)
                w.close()
                continue
            w.write(line)

    def _push_state(self) -> None:
        self._broadcast({"event": "state", **self._state()})

    def _drop_subscriber(self, w: asyncio.StreamWriter) -> None:
        self._subscribers.discard(w)
        if not self._subscribers:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result(False)

    async def _confirm(self, title: str, message: str) -> bool:
        if not self._subscribers:
            log.info("confirmación denegada: no hay suscriptores")
            return False
        cid = secrets.token_hex(8)
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        log.info("confirmación %s pedida a %d suscriptores", cid, len(self._subscribers))
        self._broadcast({"event": "confirm", "id": cid, "title": title, "message": message,
                         "timeout_s": self.confirm_timeout})
        try:
            allowed = await asyncio.wait_for(fut, self.confirm_timeout)
        except TimeoutError:
            allowed = False
            log.info("confirmación %s sin respuesta: denegada", cid)
        finally:
            self._pending.pop(cid, None)
        log.info("confirmación %s: %s", cid, "permitida" if allowed else "denegada")
        return allowed

    # --- connections ---------------------------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, kind: str) -> None:
        conns = self._conns[kind]
        uid = _peer_uid(writer)
        if uid != os.getuid() or len(conns) >= MAX_CONNECTIONS:
            log.warning("conexión rechazada en %s (uid %s, %d abiertas)", kind, uid, len(conns))
            writer.close()
            return
        conns.add(writer)
        try:
            while True:
                try:
                    if writer in self._subscribers:
                        line = await reader.readline()
                    else:
                        line = await asyncio.wait_for(reader.readline(), self.idle_timeout)
                except TimeoutError:
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    log.warning("pedido de más de %d bytes en %s: conexión cerrada", MAX_REQUEST, kind)
                    await self._send(writer, _err(f"pedido demasiado grande (máximo {MAX_REQUEST} bytes)"))
                    break
                if not line:
                    break
                req, problem = self._parse(line)
                if problem is not None:
                    await self._send(writer, _err(problem))
                    continue
                if kind == "agent":
                    res = await self._agent(req)
                    await self._send(writer, res)
                else:
                    await self._control(req, writer)
        except (ConnectionError, OSError):
            pass
        finally:
            conns.discard(writer)
            if writer in self._subscribers:
                self._drop_subscriber(writer)
            writer.close()

    @staticmethod
    def _parse(line: bytes) -> tuple[dict[str, Any], str | None]:
        try:
            req = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError):
            return {}, "JSON inválido"
        if not isinstance(req, dict):
            return {}, "el pedido debe ser un objeto JSON"
        if not isinstance(req.get("op"), str):
            return {}, "falta op (texto)"
        return req, None

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload) + "\n").encode())
        with contextlib.suppress(ConnectionError, TimeoutError):
            await asyncio.wait_for(writer.drain(), 5)

    # --- control.sock --------------------------------------------------------------------

    async def _control(self, req: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        op = req["op"]
        if op == "status":
            await self._send(writer, {"ok": True, **self._state()})
        elif op == "grant":
            minutes = _number(req.get("minutes", MAX_GRANT_MINUTES))
            if minutes is None or minutes <= 0:
                await self._send(writer, _err("minutes debe ser un número mayor que 0 (máximo 30)"))
                return
            expires_at = self._grant(min(minutes, MAX_GRANT_MINUTES))
            log.info("permiso concedido hasta %s", expires_at)
            await self._send(writer, {"ok": True, "expires_at": expires_at})
            self._push_state()
        elif op == "revoke":
            self._clear_permission()
            log.info("permiso revocado")
            await self._send(writer, {"ok": True})
            self._push_state()
        elif op == "subscribe":
            if writer in self._subscribers:
                await self._send(writer, _err("ya suscrito"))
                return
            writer.write((json.dumps({"ok": True}) + "\n").encode())
            writer.write((json.dumps({"event": "state", **self._state()}) + "\n").encode())
            self._subscribers.add(writer)
            log.info("suscriptor nuevo (%d)", len(self._subscribers))
            with contextlib.suppress(ConnectionError, TimeoutError):
                await asyncio.wait_for(writer.drain(), 5)
        elif op == "answer":
            cid, allow = req.get("id"), req.get("allow")
            if not isinstance(cid, str) or not isinstance(allow, bool):
                await self._send(writer, _err("answer necesita id (texto) y allow (true o false)"))
                return
            fut = self._pending.pop(cid, None)
            if fut is None or fut.done():
                await self._send(writer, _err("confirmación desconocida o ya respondida"))
                return
            fut.set_result(allow)
            await self._send(writer, {"ok": True})
        else:
            await self._send(writer, _err(f"op desconocida en control.sock: {op[:40]!r}"))

    # --- agent.sock ----------------------------------------------------------------------

    async def _agent(self, req: dict[str, Any]) -> dict[str, Any]:
        op = req["op"]
        if not self._limiter():
            log.warning("límite de acciones superado (%s)", op[:20])
            return _err(RATE)
        try:
            if op == "open":
                return await self._open(req)
            if op == "apps":
                return await self._apps(req)
            if op in ("type", "key", "screenshot", "windows", "focus"):
                if not self._enabled():
                    log.info("%s denegado: sin permiso", op)
                    return _err(OFF)
                async with self._action_lock:
                    if op == "type":
                        return await self._type(req)
                    if op == "key":
                        return await self._key(req)
                    if op == "windows":
                        return await self._windows()
                    if op == "focus":
                        return await self._focus_window(req)
                    return await self._screenshot(req)
            return _err(f"op desconocida en agent.sock: {op[:40]!r}")
        except Deny as e:
            log.info("%s denegado: %s", op, e)
            return _err(str(e))

    def _resolve(self, app_id: str, url: str | None) -> launch.Launch:
        """S15 checks on the helper's own environment (XDG dirs, HOME), never the caller's."""
        home = Path(os.environ.get("HOME") or Path.home())
        try:
            return launch.resolve(app_id, checks.desktop_data_dirs(os.environ, home=home), home=home,
                                  search_path=launch.LAUNCH_PATH, url=url,
                                  extra_untrusted=(*launch.SYSTEM_UNTRUSTED, self.runtime_dir),
                                  is_writable=launch.is_writable)
        except launch.Refused as e:
            raise Deny(str(e)) from None

    async def _default_browser(self) -> str:
        none = ("No hay un navegador por defecto que pueda usar (xdg-mime query default "
                "x-scheme-handler/https); no se abre la URL.")
        try:
            code, out = await self._run(["xdg-mime", "query", "default", "x-scheme-handler/https"],
                                        timeout=NIRI_TIMEOUT_S, capture=True, track=False, max_out=4096)
        except Deny:
            raise Deny(none) from None
        try:
            app_id = launch.parse_default_handler(out.decode("utf-8")) if code == 0 else None
        except UnicodeDecodeError:
            app_id = None
        if app_id is None:
            raise Deny(none)
        return app_id

    async def _open(self, req: dict[str, Any]) -> dict[str, Any]:
        has_app, has_url = "app" in req, "url" in req
        if has_app == has_url:
            raise Deny("open necesita exactamente uno de app o url")
        if has_app:
            problem = checks.validate_desktop_id(req["app"])
        else:
            problem = checks.validate_url(req["url"])
        if problem:
            raise Deny(problem)
        if has_app:
            target = await asyncio.to_thread(self._resolve, req["app"], None)
            log.info("open %s -> %s", target.app_id, target.executable)
        else:
            browser = await self._default_browser()
            try:
                target = await asyncio.to_thread(self._resolve, browser, req["url"])
            except Deny as e:
                raise Deny(f"El navegador por defecto ({browser}) no se puede usar: {e}") from None
            log.info("open url %s con %s -> %s", req["url"].split("?")[0][:80], target.app_id,
                     target.executable)
        argv = target.argv
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, executable=target.executable, cwd=target.cwd, env=launch.launch_env(os.environ),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise Deny(f"no pude ejecutar {argv[0]}: {e.strerror}") from None
        try:
            code = await asyncio.wait_for(asyncio.shield(proc.wait()), OPEN_WAIT_S)
        except TimeoutError:
            task = asyncio.create_task(proc.wait())  # detached: keep reaping it
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            return {"ok": True}
        if code != 0:
            raise Deny(f"{argv[0]} terminó con código {code}")
        return {"ok": True}

    async def _apps(self, req: dict[str, Any]) -> dict[str, Any]:
        query = req.get("query", "")
        if (not isinstance(query, str) or len(query) > checks.MAX_QUERY_CHARS):
            raise Deny(f"query debe ser texto de hasta {checks.MAX_QUERY_CHARS} caracteres")
        dirs = checks.desktop_data_dirs(os.environ)
        apps, truncated = await asyncio.to_thread(checks.list_apps, dirs, query=query)
        log.info("apps: %d entradas%s", len(apps), " (recortado)" if truncated else "")
        res: dict[str, Any] = {"ok": True, "apps": apps}
        if truncated:
            res["truncated"] = True
        return res

    async def _list_windows(self) -> list[checks.Window]:
        unreadable = "No pude leer la lista de ventanas (niri)."
        try:
            code, out = await self._run(["niri", "msg", "-j", "windows"], timeout=NIRI_TIMEOUT_S,
                                        capture=True, track=False)
        except Deny:
            raise Deny(unreadable) from None
        if code != 0:
            raise Deny(unreadable)
        try:
            return checks.parse_windows(out.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Deny(unreadable) from None

    async def _windows(self) -> dict[str, Any]:
        windows = await self._list_windows()
        log.info("windows: %d", len(windows))
        return {"ok": True, "windows": [
            {"id": w.id, "app_id": w.app_id, "title": w.title[:checks.MAX_TITLE_CHARS], "focused": w.focused}
            for w in windows]}

    async def _target_window(self, wid: object) -> checks.FocusedWindow:
        """The `focus` checks: a listed window that is neither blocklisted nor a terminal."""
        if not isinstance(wid, int) or isinstance(wid, bool) or not 0 <= wid < 2**63:
            raise Deny("window_id debe ser un entero (el id de pc_list_windows)")
        window = next((w for w in await self._list_windows() if w.id == wid), None)
        if window is None:
            raise Deny(f"No hay ninguna ventana con id {wid}.")
        target = window.as_focused()
        reason = checks.blocked_reason(target, screenshot=False, subject="La ventana")
        if reason:
            raise Deny(reason)
        return target

    async def _focus_window(self, req: dict[str, Any]) -> dict[str, Any]:
        window = await self._target_window(req.get("window_id"))
        log.info("focus %d (%s)", window.id, window.app_id[:60])
        await self._focus_and_confirm(window.id)
        return {"ok": True}

    async def _focus_and_confirm(self, wid: int) -> None:
        code, _ = await self._run(["niri", "msg", "action", "focus-window", "--id", str(wid)],
                                  timeout=NIRI_TIMEOUT_S, capture=False, track=True)
        if code != 0:
            raise Deny(f"niri no pudo enfocar la ventana (código {code})")
        deadline = time.monotonic() + FOCUS_CONFIRM_S
        while True:
            focused = await self._focus()
            if focused is not None and focused.id == wid:
                return
            if time.monotonic() >= deadline:
                raise Deny("No pude confirmar que la ventana quedó enfocada.")
            await asyncio.sleep(FOCUS_POLL_S)

    async def _run(self, argv: list[str], *, timeout: float, capture: bool, track: bool,
                   max_out: int = 1024 * 1024) -> tuple[int, bytes]:
        """Run argv (no shell). Tracked children are killed on revoke/expiry -> Deny."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError as e:
            raise Deny(f"no pude ejecutar {argv[0]}: {e.strerror}") from None
        if track:
            self._children.add(proc)

        async def collect() -> bytes:
            if not capture or proc.stdout is None:
                await proc.wait()
                return b""
            chunks, size = [], 0
            while chunk := await proc.stdout.read(65536):
                size += len(chunk)
                if size > max_out:
                    proc.kill()
                    raise Deny(f"{argv[0]}: salida demasiado grande")
                chunks.append(chunk)
            await proc.wait()
            return b"".join(chunks)

        try:
            out = await asyncio.wait_for(collect(), timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise Deny(f"{argv[0]} tardó demasiado") from None
        finally:
            self._children.discard(proc)
        if proc in self._killed:
            self._killed.discard(proc)
            raise Deny("Permiso revocado durante la acción; se cortó.")
        return proc.returncode or 0, out

    async def _focus(self) -> checks.FocusedWindow | None:
        unreadable = "No pude leer la ventana enfocada (niri); por seguridad no se hace nada."
        try:
            code, out = await self._run(["niri", "msg", "-j", "focused-window"], timeout=NIRI_TIMEOUT_S,
                                        capture=True, track=False)
        except Deny:
            raise Deny(unreadable) from None
        if code != 0:
            raise Deny(unreadable)
        try:
            return checks.parse_focused_window(out.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Deny(unreadable) from None

    async def _focused_for_input(self) -> checks.FocusedWindow:
        window = await self._focus()
        if window is None:
            raise Deny("No hay ninguna ventana enfocada; no se escribe.")
        reason = checks.blocked_reason(window, screenshot=False)
        if reason:
            raise Deny(reason)
        return window

    @staticmethod
    def _intent(req: dict[str, Any]) -> str:
        intent = req.get("intent", "")
        if not isinstance(intent, str) or len(intent) > checks.MAX_INTENT_CHARS:
            raise Deny(f"intent debe ser texto de hasta {checks.MAX_INTENT_CHARS} caracteres")
        return intent

    async def _restore_focus(self, prev_id: int | None, target_id: int) -> None:
        """Best effort (S16): give focus back to the user's window if it still exists."""
        if prev_id is None or prev_id == target_id:
            return
        try:
            await asyncio.sleep(RESTORE_DELAY_S)
            if not any(w.id == prev_id for w in await self._list_windows()):
                log.info("foco no devuelto: la ventana %d ya no existe", prev_id)
                return
            code, _ = await self._run(["niri", "msg", "action", "focus-window", "--id", str(prev_id)],
                                      timeout=NIRI_TIMEOUT_S, capture=False, track=False)
            if code != 0:
                log.warning("no pude devolver el foco a %d (código %d)", prev_id, code)
            else:
                log.info("foco devuelto a %d", prev_id)
        except Deny as e:
            log.warning("no pude devolver el foco a %d: %s", prev_id, e)

    async def _input(self, *, req: dict[str, Any], argv: list[str], intent: str, risky: list[str],
                     presses_return: bool, what: str, shown: str) -> dict[str, Any]:
        # S16: with window_id the checks and the confirmation are about the target window; it is
        # focused only after the confirmation (answered in the TUI), and focus goes back afterwards.
        target = await self._target_window(req["window_id"]) if "window_id" in req else None
        window = target if target is not None else await self._focused_for_input()
        reasons = []
        if risky:
            reasons.append("palabras de riesgo: " + ", ".join(s + "*" for s in risky))
        if presses_return and checks.needs_return_confirm(window.app_id):
            reasons.append(f"Return en {window.app_id} puede enviar algo")
        if reasons:
            message = (f"{what} en {window.app_id or '?'} («{window.title[:80]}»). "
                       f"{'; '.join(reasons)}. Intención: {intent[:200] or '—'}. {shown}")
            if not await self._confirm(f"¿Permitir que Jarvis {what}?", message):
                raise Deny("El usuario no confirmó la acción (denegada, sin respuesta o sin TUI conectada).")
            if not self._enabled():
                raise Deny(OFF)
        prev_id: int | None = None
        try:
            if target is not None:
                current = await self._focus()
                prev_id = current.id if current is not None else None
                if prev_id != target.id:
                    log.info("enfocando %d (%s) para escribir", target.id, target.app_id[:60])
                    await self._focus_and_confirm(target.id)
                    await asyncio.sleep(FOCUS_SETTLE_S)
            again = await self._focus()  # re-check right before wtype (against the target, if any)
            if again is None or again.id != window.id:
                raise Deny("El foco cambió antes de escribir; no se hizo nada.")
            reason = checks.blocked_reason(again, screenshot=False)
            if reason:
                raise Deny(reason)
            code, _ = await self._run(argv, timeout=WTYPE_TIMEOUT_S, capture=False, track=True)
            if code != 0:
                raise Deny(f"wtype terminó con código {code}")
        finally:
            if target is not None:
                await self._restore_focus(prev_id, target.id)
        return {"ok": True} if target is None else {"ok": True, "window_id": target.id}

    async def _type(self, req: dict[str, Any]) -> dict[str, Any]:
        text = req.get("text")
        problem = checks.validate_text(text)
        if problem:
            raise Deny(problem)
        assert isinstance(text, str)
        intent = self._intent(req)
        log.info("type: %s; intent %s", checks.preview(text), checks.preview(intent))
        look = checks.lookalike_stems(text, intent)
        if look:
            raise Deny("Texto con letras que imitan palabras de riesgo (" + ", ".join(look) + "); no se escribe.")
        return await self._input(req=req, argv=["wtype", *register_chars(text), *WTYPE_PRELUDE, "-d", WTYPE_KEY_DELAY_MS, "--", text], intent=intent,
                                 risky=checks.risky_stems(text, intent), presses_return="\n" in text,
                                 what="escriba", shown=f"Texto: {text[:300]}")

    async def _key(self, req: dict[str, Any]) -> dict[str, Any]:
        keys = req.get("keys")
        try:
            args = checks.parse_keys(keys)  # type: ignore[arg-type]
        except ValueError as e:
            raise Deny(str(e)) from None
        assert isinstance(keys, str)
        intent = self._intent(req)
        log.info("key: %s; intent %s", keys[:60], checks.preview(intent))
        look = checks.lookalike_stems(intent)
        if look:
            raise Deny("Intención con letras que imitan palabras de riesgo (" + ", ".join(look) + ").")
        paced: list[str] = []
        for i, a in enumerate(args):
            paced.append(a)
            # a bare Escape followed by more keys (not one held with modifiers)
            if a == "Escape" and i > 0 and args[i - 1] == "-k" and i < len(args) - 1 and args[i + 1] != "-m":
                paced += ["-s", ESCAPE_PAUSE_MS]
        return await self._input(req=req, argv=["wtype", *WTYPE_KEY_PRELUDE, *paced], intent=intent, risky=checks.risky_stems(intent),
                                 presses_return=checks.presses_return(args), what=f"pulse {keys[:60]}",
                                 shown="")

    async def _screenshot(self, req: dict[str, Any]) -> dict[str, Any]:
        argv = ["grim"]
        if "scale" in req:
            scale = _number(req["scale"])
            if scale is None or not 0.05 <= scale <= 1:
                raise Deny("scale debe ser un número entre 0.05 y 1")
            argv += ["-s", f"{scale:g}"]
        argv.append("-")
        window = await self._focus()
        if window is not None:
            reason = checks.blocked_reason(window, screenshot=True)
            if reason:
                raise Deny(reason)
        code, png = await self._run(argv, timeout=GRIM_TIMEOUT_S, capture=True, track=True,
                                    max_out=MAX_PNG_BYTES)
        if code != 0 or not png:
            raise Deny(f"grim falló (código {code})")
        log.info("screenshot: %d bytes", len(png))
        return {"ok": True, "png_base64": base64.b64encode(png).decode("ascii")}


async def run(helper: Helper) -> None:
    """Serve until SIGTERM/SIGINT."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await helper.start()
    try:
        await stop.wait()
    finally:
        log.info("saliendo; permiso revocado")
        await helper.close()
