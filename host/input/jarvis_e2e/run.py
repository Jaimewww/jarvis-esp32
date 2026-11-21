"""Desktop E2E for PC control: the real helper, real wtype, a real browser and VS Code + Vim.

The helper (`jarvis_input.server.Helper`) runs in this process on a temp runtime dir whose
environment points at a nested niri; the cases talk to its agent.sock exactly like the agent
does (`type`/`key`/`screenshot` with `window_id`), so the wtype argv, the focus switch, the
settle pause and the focus restore are the production code. Results are read back over CDP.

Exit codes: 0 all cases passed, 1 some case failed, 77 skipped (requirements missing).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import traceback
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_input import server

from . import cdp
from .desktop import Desktop, SetupError, missing_requirements

EXIT_PASS, EXIT_FAIL, EXIT_SKIP = 0, 1, 77
BROWSERS = ("brave", "chromium", "google-chrome-stable", "google-chrome")
VSCODE_ELECTRON = Path("/usr/share/code/code")
TARGET_TITLE, OTHER_TITLE = "jarvis-e2e-target", "jarvis-e2e-other"
HOLA = "hola mundo"
SENTENCE = "Hola mundo, este es un texto de prueba. ’ — …"  # L27 + legacy keysyms (ad4cf60)
VIM_CLEAR = "Escape g g d shift+g"
MARK_CSS, MARK_RGB = "rgb(255, 0, 255)", (255, 0, 255)  # the target page colour in the screenshot case
SELECT_DELETE = "ctrl+a Delete"
# More distinct characters than one wtype keymap has safe keycodes for (several wtype runs).
WIDE = ("abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789 "
        ".,;:!?¿¡()[]{}<>@#$%&*+=/_\"'~^| áéíóúñü ÁÉÍÓÚÑÜ")
# KeyboardEvent.code values of character keys: a typed character on any other code (Backspace,
# Tab, Enter, Escape, F-keys, arrows, modifiers…) is what VS Code dispatches shortcuts on (L2).
CHAR_CODE = re.compile(r"Key[A-Z]|Digit[0-9]|Minus|Equal|BracketLeft|BracketRight|Semicolon|Quote|"
                       r"Backquote|Backslash|Comma|Period|Slash|Space|IntlBackslash|Numpad[0-9]|"
                       r"Numpad(Decimal|Add|Subtract|Multiply|Divide|Equal|Comma)")

PAGE = """<!doctype html><meta charset="utf-8"><title>{title}</title>
<textarea id="t" rows="6" cols="60" autofocus></textarea>
<script>
window.__keys = [];
for (const type of ["keydown", "keyup"]) addEventListener(type, e =>
  window.__keys.push({{type, key: e.key, code: e.code}}), true);
document.getElementById("t").focus();
</script>
"""


@dataclass
class Result:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


def summarize(results: list[Result]) -> tuple[str, int]:
    """Summary line and exit code: any FAIL -> 1; no PASS at all -> 77; else 0."""
    counts = {s: sum(r.status == s for r in results) for s in ("PASS", "FAIL", "SKIP")}
    line = f"summary: {counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped"
    if counts["FAIL"]:
        return line, EXIT_FAIL
    if not counts["PASS"]:
        return line, EXIT_SKIP
    return line, EXIT_PASS


def png_pixels(data: bytes) -> tuple[int, int, list[tuple[int, int, int]]]:
    """(width, height, RGB pixels) of an 8-bit, non-interlaced RGB/RGBA PNG (what grim writes)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, ihdr = 8, b"", b""
    while pos < len(data):
        n = int.from_bytes(data[pos:pos + 4], "big")
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            ihdr = body
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    if len(ihdr) != 13:
        raise ValueError("PNG without IHDR")
    w, h = int.from_bytes(ihdr[0:4], "big"), int.from_bytes(ihdr[4:8], "big")
    depth, ctype, interlace = ihdr[8], ihdr[9], ihdr[12]
    if depth != 8 or ctype not in (2, 6) or interlace:
        raise ValueError(f"unsupported PNG (depth {depth}, color type {ctype}, interlace {interlace})")
    bpp = 3 if ctype == 2 else 4
    raw, stride = zlib.decompress(idat), w * bpp
    prev, pixels = bytearray(stride), []
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif f != 0:
                raise ValueError(f"bad PNG filter {f}")
        pixels += [(line[x], line[x + 1], line[x + 2]) for x in range(0, stride, bpp)]
        prev = line
    return w, h, pixels


def keydowns(events: list[dict[str, str]]) -> list[tuple[str, str]]:
    """(key, code) of the keydowns, without the Shift taps of the wtype prelude."""
    return [(e["key"], e["code"]) for e in events
            if e.get("type") == "keydown" and e.get("key") != "Shift"]


def editor_text(lines: list[tuple[float, str]]) -> str:
    """Monaco `.view-line`s as (top px, text): sorted by position, nbsp -> space, joined."""
    return "\n".join(t.replace("\u00a0", " ") for _, t in sorted(lines)).rstrip("\n")


def non_char_codes(downs: list[tuple[str, str]]) -> list[str]:
    """The keydowns (key/code) of typed characters that arrived on a non-character code."""
    return [f"{k}/{c}" for k, c in downs if not CHAR_CODE.fullmatch(c)]


def vim_mode(status_texts: list[str]) -> str | None:
    for text in status_texts:
        for mode in ("NORMAL", "INSERT", "VISUAL"):
            if f"-- {mode} --" in text:
                return mode
    return None


def assert_isolated(env: Mapping[str, str], root: Path) -> None:
    """Refuse to run wtype/niri unless both point inside the temp dir (never the user's session)."""
    for key in ("WAYLAND_DISPLAY", "NIRI_SOCKET", "XDG_RUNTIME_DIR"):
        value = env.get(key, "")
        if not value.startswith(str(root) + os.sep):
            raise SetupError(f"{key}={value!r} is not inside {root}; refusing to send input")
    if env.get("DISPLAY"):
        raise SetupError("DISPLAY is set; refusing to send input")


def find_browser(which: Callable[[str], str | None]) -> str | None:
    for name in BROWSERS:
        if path := which(name):
            return path
    return None


def find_vim_extension(home: Path) -> Path | None:
    found = sorted((home / ".vscode" / "extensions").glob("vscodevim.vim-*"))
    return found[-1] if found else None


# --- the helper, in a background event loop ------------------------------------------------


class HelperThread:
    def __init__(self, runtime_dir: Path) -> None:
        self.helper = server.Helper(runtime_dir)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self) -> HelperThread:
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self.helper.start(), self.loop).result(10)
        return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(self.helper.close(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)

    @staticmethod
    def _request(path: Path, req: dict[str, Any], timeout: float = 70) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall((json.dumps(req) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data)

    def control(self, req: dict[str, Any]) -> dict[str, Any]:
        return self._request(self.helper.control_path, req)

    def agent(self, req: dict[str, Any]) -> dict[str, Any]:
        res = self._request(self.helper.agent_path, req)
        if not res.get("ok"):
            raise AssertionError(f"helper refused {req.get('op')}: {res.get('error')}")
        return res


# --- the run -------------------------------------------------------------------------------


class Run:
    def __init__(self, root: Path, desk: Desktop, helper: HelperThread, verbose: bool) -> None:
        self.root, self.desk, self.helper, self.verbose = root, desk, helper, verbose
        self.results: list[Result] = []

    def case(self, name: str, fn: Callable[[], str | None]) -> None:
        t0 = time.monotonic()
        try:
            detail = fn() or ""
            res = Result(name, "PASS", detail)
        except AssertionError as e:
            res = Result(name, "FAIL", str(e))
        except Exception as e:  # noqa: BLE001 (a crash of one case is a FAIL, the run goes on)
            res = Result(name, "FAIL", f"{type(e).__name__}: {e}")
            if self.verbose:
                traceback.print_exc()
        self.results.append(res)
        took = time.monotonic() - t0
        print(f"{res.status}: {name} ({took:.1f}s){' — ' + res.detail if res.detail else ''}", flush=True)

    def skip(self, name: str, reason: str) -> None:
        self.results.append(Result(name, "SKIP", reason))
        print(f"SKIP: {name} — {reason}", flush=True)

    def focus_other(self, other: int) -> None:
        self.helper.agent({"op": "focus", "window_id": other})

    def check_focus_back(self, other: int) -> None:
        got = self.desk.focused_id()
        assert got == other, f"focus did not go back to the user's window ({other}); focused: {got}"

    def type_into(self, wid: int, text: str) -> None:
        self.helper.agent({"op": "type", "text": text, "intent": "prueba e2e", "window_id": wid})

    def key_into(self, wid: int, keys: str) -> None:
        self.helper.agent({"op": "key", "keys": keys, "intent": "prueba e2e", "window_id": wid})


def connect_page(port: int, title: str, timeout: float = 20) -> cdp.Cdp:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError, ValueError):
            for t in cdp.list_targets(port):
                if t.get("type") == "page" and t.get("title") == title:
                    return cdp.Cdp(t["webSocketDebuggerUrl"])
        time.sleep(0.2)
    raise SetupError(f"no CDP page titled {title!r}")


def wait_port(profile: Path, proc_alive: Callable[[], bool], timeout: float = 30) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (port := cdp.devtools_port(profile)) is not None:
            return port
        if not proc_alive():
            raise SetupError(f"client exited before opening DevTools ({profile.name})")
        time.sleep(0.2)
    raise SetupError(f"no DevToolsActivePort in {profile}")


def until(fn: Callable[[], Any], timeout: float, what: str) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        with contextlib.suppress(cdp.CdpError, OSError):
            last = fn()
            if last:
                return last
        time.sleep(0.2)
    raise SetupError(f"timed out waiting for {what} (last: {last!r})")


def browser_cases(run: Run, target: cdp.Cdp, wid: int, other: int) -> None:
    def reset() -> None:
        target.evaluate("document.getElementById('t').value = ''; window.__keys = []; "
                        "document.getElementById('t').focus(); true")

    def typed(text: str) -> Callable[[], str]:
        def fn() -> str:
            reset()
            run.focus_other(other)
            run.type_into(wid, text)
            time.sleep(0.2)
            value = target.evaluate("document.getElementById('t').value")
            downs = keydowns(target.evaluate("window.__keys"))
            if value != text:
                codes = " ".join(f"{k}/{c}" for k, c in downs)
                raise AssertionError(f"textarea has {value!r}, expected {text!r}; keydowns key/code: {codes}")
            bad = non_char_codes(downs)
            assert not bad, f"characters typed on non-character keycodes (key/code): {' '.join(bad)}"
            run.check_focus_back(other)
            return "text complete on character keycodes, focus restored"
        return fn

    run.case(f"browser: type {HOLA!r} right after focus", typed(HOLA))
    run.case(f"browser: type {SENTENCE!r}", typed(SENTENCE))
    run.case(f"browser: type {len(set(WIDE))} distinct characters", typed(WIDE))

    def keys() -> str:
        reset()
        run.focus_other(other)
        run.key_into(wid, VIM_CLEAR)
        time.sleep(0.2)
        events = target.evaluate("window.__keys")
        downs = keydowns(events)
        keys_only = [k for k, _ in downs]
        assert keys_only == ["Escape", "g", "g", "d", "G"], f"keydowns {downs}"
        assert downs[0][1] == "Escape", f"Escape arrived with code {downs[0][1]!r}, not 'Escape'"
        bad = non_char_codes(downs[1:])
        assert not bad, f"letter keys on non-character keycodes (key/code): {' '.join(bad)}"
        value = target.evaluate("document.getElementById('t').value")
        assert value == "ggdG", f"textarea has {value!r}, expected 'ggdG'"
        run.check_focus_back(other)
        return "keydowns Escape(code Escape) g g d G, focus restored"

    run.case(f"browser: key {VIM_CLEAR!r} keydowns", keys)

    def select_delete() -> str:
        reset()
        target.evaluate("document.getElementById('t').value = 'texto a quitar'; window.__keys = []; true")
        run.focus_other(other)
        run.key_into(wid, SELECT_DELETE)
        time.sleep(0.2)
        downs = keydowns(target.evaluate("window.__keys"))
        value = target.evaluate("document.getElementById('t').value")
        assert downs == [("a", "KeyA"), ("Delete", "Delete")], f"keydowns key/code {downs}"
        assert value == "", f"textarea has {value!r}, expected empty"
        run.check_focus_back(other)
        return "keydowns a(KeyA) Delete(Delete), textarea empty, focus restored"

    run.case(f"browser: key {SELECT_DELETE!r} empties a textarea", select_delete)

    def screenshot() -> str:
        # T26/S17: the target page is painted magenta; the capture of its window must show it
        # (it is the nested niri's output, never the user's monitors), and focus goes back.
        target.evaluate(f"document.body.style.background = '{MARK_CSS}'; true")
        try:
            time.sleep(0.2)
            run.focus_other(other)
            res = run.helper.agent({"op": "screenshot", "window_id": wid, "scale": 0.25})
            assert res.get("window_id") == wid, f"window_id echoed {res.get('window_id')!r}, expected {wid}"
            w, h, pixels = png_pixels(base64.b64decode(res["png_base64"]))
            marked = sum(p == MARK_RGB for p in pixels)
            assert marked * 20 >= len(pixels), (
                f"{w}x{h} capture has {marked} magenta pixels of {len(pixels)}: not the target window")
            run.check_focus_back(other)
            return f"{w}x{h} PNG, {100 * marked // len(pixels)}% target colour, focus restored"
        finally:
            target.evaluate("document.body.style.background = ''; true")

    if shutil.which("grim") is None:
        run.skip("browser: screenshot with window_id", "grim not found on PATH")
    else:
        run.case("browser: screenshot with window_id", screenshot)


VIEW_LINES_JS = """(() => {
  const ed = document.querySelector('.monaco-editor .view-lines');
  if (!ed) return null;
  return [...ed.querySelectorAll('.view-line')].map(l => [parseFloat(l.style.top) || 0, l.textContent]);
})()"""
STATUS_JS = "[...document.querySelectorAll('.statusbar-item')].map(e => e.textContent)"
# A capture-phase keydown logger in the workbench, for the failure details (key/code per press).
KEYLOG_JS = """window.__keys = []; if (!window.__keylog) { window.__keylog = true;
  addEventListener('keydown', e => window.__keys.push({type: 'keydown', key: e.key, code: e.code}), true); } true"""


def vscode_cases(run: Run, ws: cdp.Cdp, wid: int, other: int) -> None:
    def text() -> str:
        lines = ws.evaluate(VIEW_LINES_JS)
        return editor_text([(t, s) for t, s in lines]) if lines is not None else "<no editor>"

    def mode() -> str | None:
        return vim_mode(ws.evaluate(STATUS_JS) or [])

    def typed(s: str) -> Callable[[], str]:
        def fn() -> str:
            assert text() == "", f"buffer not empty before typing: {text()!r}"
            ws.evaluate(KEYLOG_JS)
            run.focus_other(other)
            run.key_into(wid, "i")
            got_mode = mode()
            assert got_mode == "INSERT", f"vim mode after 'i' is {got_mode}"
            run.check_focus_back(other)
            run.type_into(wid, s)
            time.sleep(0.3)
            got = text()
            downs = keydowns(ws.evaluate("window.__keys"))
            if got != s:
                codes = " ".join(f"{k}/{c}" for k, c in downs)
                raise AssertionError(f"buffer has {got!r}, expected {s!r}; keydowns key/code: {codes}")
            bad = non_char_codes(downs[1:])  # downs[0] is the 'i' key
            assert not bad, f"characters typed on non-character keycodes (key/code): {' '.join(bad)}"
            run.check_focus_back(other)
            return "text complete in insert mode, focus restored"
        return fn

    def clear() -> str:
        before = text()
        run.focus_other(other)
        run.key_into(wid, VIM_CLEAR)
        time.sleep(0.3)
        got, got_mode = text(), mode()
        assert got == "", f"buffer has {got!r} (was {before!r}), expected empty; vim mode {got_mode}"
        assert got_mode == "NORMAL", f"vim mode {got_mode}, expected NORMAL"
        run.check_focus_back(other)
        return f"cleared {len(before)} chars, NORMAL mode, focus restored"

    run.case(f"vscode+vim: key 'i' then type {HOLA!r}", typed(HOLA))
    run.case(f"vscode+vim: key {VIM_CLEAR!r} empties the buffer", clear)
    run.case(f"vscode+vim: key 'i' then type {SENTENCE!r}", typed(SENTENCE))
    run.case(f"vscode+vim: key {VIM_CLEAR!r} empties it again", clear)


def execute(root: Path, args: argparse.Namespace) -> list[Result]:
    which = shutil.which
    browser = find_browser(which)
    reason = missing_requirements(os.environ, which, browser)
    if reason:
        return [Result("requirements", "SKIP", reason)]
    assert browser is not None
    vim_ext = find_vim_extension(Path.home())
    vscode_reason = None
    if args.no_vscode:
        vscode_reason = "--no-vscode"
    elif not VSCODE_ELECTRON.is_file():
        vscode_reason = f"VS Code not found at {VSCODE_ELECTRON}"
    elif vim_ext is None:
        vscode_reason = "vscodevim not installed in ~/.vscode/extensions"

    desk = Desktop(root, os.environ)
    try:
        desk.start_niri()
        pages = {}
        for title in (OTHER_TITLE, TARGET_TITLE):
            page = root / f"{title}.html"
            page.write_text(PAGE.format(title=title))
            pages[title] = page.as_uri()
        profile = desk.start_browser(browser, [pages[OTHER_TITLE]])
        port = wait_port(profile, lambda: desk.procs[-1][1].poll() is None)
        other_cdp = connect_page(port, OTHER_TITLE)
        other_cdp.call("Target.createTarget", {"url": pages[TARGET_TITLE], "newWindow": True})
        other_cdp.close()
        target = connect_page(port, TARGET_TITLE)
        until(lambda: target.evaluate("document.readyState") == "complete", 10, "target page")
        wid = desk.wait_window(lambda w: str(w.get("title", "")).startswith(TARGET_TITLE), 15)
        other = desk.wait_window(lambda w: str(w.get("title", "")).startswith(OTHER_TITLE), 15)

        vscode = None
        if vscode_reason is None:
            doc = root / "doc.txt"
            doc.write_text("")
            data = desk.start_vscode(VSCODE_ELECTRON, vim_ext, doc)  # type: ignore[arg-type]
            vproc = desk.procs[-1][1]
            vport = wait_port(data, lambda: vproc.poll() is None, 40)
            vcdp: cdp.Cdp | None = None  # the workbench page (its title changes)
            deadline = time.monotonic() + 40
            while vcdp is None and time.monotonic() < deadline:
                with contextlib.suppress(OSError, ValueError):
                    for t in cdp.list_targets(vport):
                        if t.get("type") == "page" and "workbench" in t.get("url", ""):
                            vcdp = cdp.Cdp(t["webSocketDebuggerUrl"])
                            break
                time.sleep(0.3)
            if vcdp is None:
                raise SetupError("no CDP page for the VS Code workbench")
            until(lambda: vim_mode(vcdp.evaluate(STATUS_JS) or []) == "NORMAL", 40,
                  "vscodevim in NORMAL mode")
            vwid = desk.wait_window(lambda w: str(w.get("title", "")).startswith("jarvis-e2e-vscode"), 15)
            vscode = (vcdp, vwid)

        os.environ.update(desk.env)  # the helper's children (wtype, niri msg) use os.environ
        for key in ("DISPLAY",):
            os.environ.pop(key, None)
        assert_isolated(os.environ, root)
        with HelperThread(desk.runtime_dir) as helper:
            helper.control({"op": "grant", "minutes": 5})
            run = Run(root, desk, helper, args.verbose)
            browser_cases(run, target, wid, other)
            if vscode is not None:
                vscode_cases(run, vscode[0], vscode[1], other)
            else:
                run.skip("vscode+vim cases", vscode_reason or "")
            return run.results
    finally:
        desk.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m jarvis_e2e", description=__doc__.splitlines()[0])
    parser.add_argument("--no-vscode", action="store_true", help="only the browser cases")
    parser.add_argument("--keep", action="store_true", help="keep the temp dir (logs) for debugging")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    def stop(signum: int, _frame: object) -> None:  # unwind so the finally blocks tear down
        raise KeyboardInterrupt(f"signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    t0 = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="jarvis-e2e-"))
    try:
        try:
            results = execute(root, args)
        except SetupError as e:
            results = [Result("setup", "FAIL", str(e))]
            for log in sorted(root.glob("*.log")):  # the temp dir goes away: show the tails
                tail = log.read_text(errors="replace").splitlines()[-5:]
                print(f"--- {log.name}\n" + "\n".join(tail), file=sys.stderr)
        except KeyboardInterrupt as e:
            print(f"interrupted ({e or 'SIGINT'}); tore down", file=sys.stderr)
            return EXIT_FAIL
        for r in results:
            if r.name in ("requirements", "setup"):
                print(f"{r.status}: {r.name} — {r.detail}")
        line, code = summarize(results)
        print(f"{line} in {time.monotonic() - t0:.0f}s")
        return code
    finally:
        if args.keep:
            print(f"kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)
