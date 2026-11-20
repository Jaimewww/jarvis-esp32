"""``/login`` in the window: the whole sign-in through the real client and a fake daemon.

The fake daemon plays protocol v1 the way upstream ``bridge.ts`` and ``auth.ts`` do: it
lists the accounts on ``auth_list``, refuses or starts a flow on ``auth_login``, asks
through ``auth_prompt``, reports through ``auth_event`` and ends with ``auth_done``. No
real account is touched.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import fake_server, frame, wait_for

from jarvis.devview import DeveloperView
from jarvis.loginview import LoginPanel
from jarvis.tui import JarvisApp, KeysFooter
from jarvis.userview import UserView

SECRET = "sk-ant-SECRET4242xyz"
NO_ACCOUNT = "Connect an AI account first: open Accounts or type /login."
UNSUPPORTED = "Signing in with a Claude subscription is not supported. Use an Anthropic API key instead."
AUTH_URL = "https://auth.openai.test/oauth/authorize?client_id=app_jarvis&state=abc123"
PROVIDERS = [
    {"id": "anthropic", "name": "Anthropic", "methods": ["api_key"], "configured": False},
    {"id": "openai-codex", "name": "ChatGPT", "methods": ["oauth", "api_key"],
     "oauthName": "ChatGPT Plus/Pro", "configured": False},
]
IDLE = frame(
    {"type": "esp32_snapshot",
     "devices": [{"device_id": "esp32-s3", "state": "idle", "rssi": -50, "ip": "127.0.0.1", "online": True}]}
)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
Script = Callable[[dict[str, Any], Emit], Awaitable[None]]


def _screen(app: JarvisApp) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", app.export_screenshot())).replace("\xa0", " ")


def _flat(text: str) -> str:
    # The SVG export drops runs of spaces between differently styled cells.
    return re.sub(r"\s+", "", text)


def _has(screen: str, phrase: str) -> bool:
    return _flat(phrase) in _flat(screen)


#: The open connection of the fake daemon: ``emit`` and ``socket``, for tests that act
#: as the daemon on their own (a late frame, a dropped connection).
LINK: dict[str, Any] = {}


def _daemon(sent: list[dict[str, Any]], script: Script) -> Callable[[Any], Awaitable[None]]:
    async def daemon(socket: Any) -> None:
        seq = 0

        async def emit(payload: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            await socket.send(json.dumps({**payload, "seq": seq}))

        LINK.update(emit=emit, socket=socket)

        async for raw in socket:
            message = json.loads(raw)
            sent.append(message)
            kind = message.get("type")
            if kind == "hello":
                await emit({"type": "ready", "agentState": "idle", "model": "unknown",
                            "features": {"conversations": False}})
            elif kind == "auth_list":
                await emit({"type": "auth_providers", "providers": PROVIDERS})
            elif kind == "send":
                await emit({"type": "error", "code": "no_account", "message": NO_ACCOUNT})
            else:
                await script(message, emit)

    return daemon


async def _backend(socket: Any) -> None:
    await socket.send(IDLE)
    await socket.wait_closed()


async def _type(pilot: Any, app: JarvisApp, text: str) -> None:
    """Type ``text`` into the prompt key by key, then enter."""

    await pilot.press(*text)
    await pilot.press("enter")
    await pilot.pause()


def _run(tmp_path: Path, script: Script, body: Callable[[Any, JarvisApp], Awaitable[Any]],
         size: tuple[int, int] = (140, 40)) -> Any:
    token = tmp_path / "token"
    token.write_text("daemon-token\n", encoding="utf-8")
    sent: list[dict[str, Any]] = []

    async def scenario() -> Any:
        async with fake_server(_backend) as port, fake_server(_daemon(sent, script)) as daemon_port:
            app = JarvisApp(
                url=f"ws://127.0.0.1:{daemon_port}/ws",
                token_path=token,
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
            )
            async with app.run_test(size=size) as pilot:
                await wait_for(lambda: app._host.agent_status == "idle", timeout=5.0)
                await pilot.click("#prompt")
                return await body(pilot, app)

    return asyncio.run(scenario()), sent


# --- API key: the secret is masked, sent once, and never kept or logged -------------


def test_login_with_an_api_key_masks_the_secret_and_clears_the_sign_in_notice(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def script(message: dict[str, Any], emit: Emit) -> None:
        if message["type"] == "auth_login":
            await emit({"type": "auth_prompt", "prompt": {
                "flowId": "f1", "kind": "secret", "message": "Paste your Anthropic API key",
                "placeholder": "sk-ant-…"}})
        elif message["type"] == "auth_reply" and message.get("value"):
            await emit({"type": "auth_event", "flowId": "f1",
                        "event": {"kind": "progress", "message": "Checking the key…"}})
            await emit({"type": "auth_done", "flowId": "f1", "providerId": "anthropic", "ok": True})

    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        out: dict[str, Any] = {}
        await _type(pilot, app, "hola")
        await wait_for(lambda: app._host.agent_notice is not None, timeout=5.0)
        await _type(pilot, app, "/login")
        await wait_for(lambda: _has(_screen(app), "Anthropic"), timeout=5.0)
        out["list"] = _screen(app)
        out["list_footer"] = app.query_one(KeysFooter).render().plain
        await _type(pilot, app, "1")
        await wait_for(lambda: _has(_screen(app), "Paste your Anthropic API key"), timeout=5.0)
        await pilot.press(*SECRET)
        await pilot.pause()
        out["typing"] = _screen(app)
        await pilot.press("enter")
        await wait_for(lambda: _has(_screen(app), "Signed in to Anthropic."), timeout=5.0)
        out["done_user"] = _screen(app)
        out["notice"] = app._host.agent_notice
        out["model"] = repr(vars(app._host)) + repr(app._host.login)
        out["input"] = app.query_one("#prompt").value
        await pilot.press("f2")
        await pilot.pause()
        out["done_developer"] = _screen(app)
        await pilot.press("escape")
        await pilot.pause()
        out["closed"] = app.query_one(LoginPanel).display
        return out

    out, sent = _run(tmp_path, script, body)

    # /login is handled here: it never reaches the assistant as a message.
    assert {"type": "send", "text": "/login"} not in sent
    for phrase in ("Sign in", "Anthropic", "not connected", "API key", "ChatGPT",
                   "subscription (ChatGPT Plus/Pro)"):
        assert _has(out["list"], phrase), phrase
    assert "esc close" in out["list_footer"]
    # One method: the choice of a provider starts the sign-in at once.
    assert {"type": "auth_login", "providerId": "anthropic", "method": "api_key"} in sent
    # The secret reaches the daemon once, as typed ...
    assert sent.count({"type": "auth_reply", "flowId": "f1", "value": SECRET}) == 1
    # ... and is never on screen, in the model, in the input after sending or in a log.
    assert "•" * 8 in out["typing"]
    for screen in ("typing", "done_user", "done_developer"):
        assert SECRET not in out[screen] and "SECRET4242" not in _flat(out[screen]), screen
    assert SECRET not in out["model"]
    assert out["input"] == ""
    # Every log record of this window (the fake daemon's own server logger aside).
    window_logs = [r.getMessage() for r in caplog.records if not r.name.startswith("websockets.server")]
    assert window_logs, "debug logging was on"
    assert not [line for line in window_logs if SECRET in line]
    assert _has(out["done_developer"], "Signed in to Anthropic.")
    assert out["notice"] is None  # the sign-in notice goes once an account is connected
    assert not _has(out["done_user"], NO_ACCOUNT)
    assert out["closed"] is False


# --- Subscription: links, device code, a choice, a pasted code and Esc ----------------


def test_subscription_sign_in_shows_the_address_and_code_and_esc_cancels(tmp_path: Path) -> None:
    async def script(message: dict[str, Any], emit: Emit) -> None:
        if message["type"] == "auth_login":
            await emit({"type": "auth_event", "flowId": "f2", "event": {
                "kind": "auth_url", "url": AUTH_URL, "instructions": "Open this page in your browser."}})
            await emit({"type": "auth_event", "flowId": "f2", "event": {
                "kind": "device_code", "userCode": "WXYZ-9876", "verificationUri": "https://dev.openai.test/device",
                "expiresInSeconds": 900}})
            await emit({"type": "auth_event", "flowId": "f2", "event": {
                "kind": "info", "message": "Read the terms.", "links": [{"url": "https://terms.test", "label": "Terms"}]}})
            await emit({"type": "auth_prompt", "prompt": {
                "flowId": "f2", "kind": "select", "message": "Which workspace?",
                "options": [{"id": "ws-home", "label": "Home"}, {"id": "ws-work", "label": "Work"}]}})
        elif message["type"] == "auth_reply" and message.get("value") == "ws-work":
            await emit({"type": "auth_prompt", "prompt": {
                "flowId": "f2", "kind": "manual_code", "message": "Paste the code from the page"}})
        elif message["type"] == "auth_reply" and message.get("cancelled"):
            await emit({"type": "auth_done", "flowId": "f2", "providerId": "openai-codex", "ok": False,
                        "message": "Sign-in cancelled."})

    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        out: dict[str, Any] = {}
        await _type(pilot, app, "/login")
        await wait_for(lambda: _has(_screen(app), "ChatGPT"), timeout=5.0)
        await _type(pilot, app, "2")
        await pilot.pause()
        out["methods"] = _screen(app)
        await _type(pilot, app, "1")
        await wait_for(lambda: _has(_screen(app), "Which workspace?"), timeout=5.0)
        out["flow"] = _screen(app)
        out["flow_footer"] = app.query_one(KeysFooter).render().plain
        await pilot.press("f2")
        await pilot.pause()
        out["flow_developer"] = _screen(app)
        await pilot.press("f2")
        await _type(pilot, app, "7")  # not an option: a hint, nothing sent
        out["bad"] = _screen(app)
        await _type(pilot, app, "2")
        await wait_for(lambda: _has(_screen(app), "Paste the code from the page"), timeout=5.0)
        await pilot.press(*"abc")
        await pilot.pause()
        out["code"] = _screen(app)
        await pilot.press("escape")
        await wait_for(lambda: _has(_screen(app), "Sign-in cancelled."), timeout=5.0)
        out["cancelled"] = _screen(app)
        return out

    out, sent = _run(tmp_path, script, body)

    assert _has(out["methods"], "subscription (ChatGPT Plus/Pro)") and _has(out["methods"], "API key")
    assert {"type": "auth_login", "providerId": "openai-codex", "method": "oauth"} in sent
    for screen in ("flow", "flow_developer"):
        # The whole address, so it can be copied, with the daemon's instructions.
        assert _has(out[screen], AUTH_URL), screen
        assert _has(out[screen], "Open this page in your browser."), screen
        for phrase in ("WXYZ-9876", "https://dev.openai.test/device", "expires in 15 min",
                       "Read the terms.", "Terms", "https://terms.test", "1 Home", "2 Work"):
            assert _has(out[screen], phrase), (screen, phrase)
    assert "ctrl+y" in out["flow_footer"] and "esc cancel" in out["flow_footer"]
    assert _has(out["bad"], "Type a number from 1 to 2.")
    assert {"type": "auth_reply", "flowId": "f2", "value": "ws-work"} in sent
    assert not any(m.get("value") == "7" for m in sent)
    assert "abc" in _flat(out["code"])  # a pasted code is not a secret: shown as typed
    assert {"type": "auth_reply", "flowId": "f2", "cancelled": True} in sent
    assert _has(out["cancelled"], "Sign-in cancelled.")


def test_the_daemons_refusal_is_shown_as_is(tmp_path: Path) -> None:
    async def script(message: dict[str, Any], emit: Emit) -> None:
        if message["type"] == "auth_login":
            await emit({"type": "error", "code": "auth_unsupported", "message": UNSUPPORTED})

    async def body(pilot: Any, app: JarvisApp) -> str:
        await _type(pilot, app, "/login")
        await wait_for(lambda: _has(_screen(app), "ChatGPT"), timeout=5.0)
        await _type(pilot, app, "1")
        await wait_for(lambda: _has(_screen(app), UNSUPPORTED), timeout=5.0)
        return _screen(app)

    screen, sent = _run(tmp_path, script, body)
    assert _has(screen, UNSUPPORTED)
    assert _has(screen, "Anthropic")  # back on the list, to choose again


def test_the_login_panel_keeps_within_the_row_budget(tmp_path: Path) -> None:
    async def script(message: dict[str, Any], emit: Emit) -> None:
        if message["type"] == "auth_login":
            for n in range(30):
                await emit({"type": "auth_event", "flowId": "f3",
                            "event": {"kind": "progress", "message": f"step {n}"}})
            await emit({"type": "auth_prompt", "prompt": {"flowId": "f3", "kind": "text", "message": "Your name"}})

    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _type(pilot, app, "/login")
        await wait_for(lambda: _has(_screen(app), "ChatGPT"), timeout=5.0)
        await _type(pilot, app, "1")
        await wait_for(lambda: _has(_screen(app), "Your name"), timeout=5.0)
        panel = app.query_one(LoginPanel)
        lines = panel.render().plain.splitlines()
        return {
            "panel_h": panel.size.height,
            "views_h": app.query_one(UserView).size.height,
            "widths": {len(line) for line in lines},
            "w": panel.size.width,
            "screen": _screen(app),
        }

    out, _ = _run(tmp_path, script, body, size=(80, 24))
    assert out["panel_h"] <= 12
    assert out["views_h"] >= 8
    assert out["widths"] == {out["w"]}
    assert _has(out["screen"], "step 29") and not _has(out["screen"], "step 0 ")
    assert _has(out["screen"], "Your name")


def test_login_needs_the_daemon(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, list[str]]:
        async with fake_server(_backend) as port:
            app = JarvisApp(
                url="ws://127.0.0.1:9/ws",
                token_path=tmp_path / "missing",
                backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
                backend_token="t",
                animate=False,
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.click("#prompt")
                await _type(pilot, app, "/login")
                return app.query_one(LoginPanel).display, [n.message for n in app._notifications]

    shown, notes = asyncio.run(scenario())
    assert shown is False
    assert "Gentle Dot is not connected: /login needs it." in notes


def test_sign_in_notice_guides_to_login_in_both_views() -> None:
    from jarvis import devview, userview
    from jarvis.client import ErrorEvent
    from jarvis.model import HostModel

    model = HostModel()
    model.apply_daemon(ErrorEvent("no_account", "Connect an AI account first."))
    user = "\n".join("".join(t for t, _ in row) for row in userview.notice_rows(model, 100))
    dev = "\n".join("".join(t for t, _ in row) for row in devview.rail_rows(model, devview.grid(140), 20))
    assert "Type /login to connect one." in user
    assert "Type /login to connect one." in dev
    # The daemon's own text already says it: not twice.
    model.apply_daemon(ErrorEvent("no_account", NO_ACCOUNT))
    user = "\n".join("".join(t for t, _ in row) for row in userview.notice_rows(model, 100))
    assert "Type /login to connect one." not in user



# --- Regressions: a secret never outlives its prompt -----------------------------------


def _secret_prompt(flow: str = "f1") -> Script:
    async def script(message: dict[str, Any], emit: Emit) -> None:
        if message["type"] == "auth_login":
            await emit({"type": "auth_prompt", "prompt": {
                "flowId": flow, "kind": "secret", "message": "Paste your Anthropic API key"}})

    return script


async def _to_secret_prompt(pilot: Any, app: JarvisApp) -> None:
    await _type(pilot, app, "/login")
    await wait_for(lambda: _has(_screen(app), "Anthropic"), timeout=5.0)
    await _type(pilot, app, "1")
    await wait_for(lambda: _has(_screen(app), "Paste your Anthropic API key"), timeout=5.0)


def _no_secret(app: JarvisApp) -> None:
    screen = _flat(_screen(app))
    assert SECRET not in screen and "SECRET4242" not in screen
    assert "SECRET" not in app.query_one("#prompt").value


def _carrying_secret(sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in sent if SECRET in json.dumps(m)]


def test_esc_after_typing_a_secret_clears_it_and_it_never_becomes_chat(tmp_path: Path) -> None:
    async def script(message: dict[str, Any], emit: Emit) -> None:
        await _secret_prompt()(message, emit)
        if message["type"] == "auth_reply" and message.get("cancelled"):
            await emit({"type": "auth_done", "flowId": "f1", "providerId": "anthropic", "ok": False,
                        "message": "Sign-in cancelled."})

    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _to_secret_prompt(pilot, app)
        await pilot.press(*SECRET)
        await pilot.press("escape")
        await pilot.pause()
        _no_secret(app)
        await wait_for(lambda: _has(_screen(app), "Sign-in cancelled."), timeout=5.0)
        _no_secret(app)
        await pilot.press("escape")  # closes the panel
        await pilot.pause()
        _no_secret(app)
        await pilot.press("enter")  # the old value must not go out as a message
        await pilot.pause()
        await asyncio.sleep(0.2)
        return {"open": app._host.login.open}

    out, sent = _run(tmp_path, script, body)
    assert out["open"] is False
    assert {"type": "auth_reply", "flowId": "f1", "cancelled": True} in sent
    assert _carrying_secret(sent) == []
    assert not any(m["type"] == "send" for m in sent)


@pytest.mark.parametrize("ending", [
    {"type": "auth_done", "flowId": "f1", "providerId": "anthropic", "ok": False, "message": "Sign-in did not finish. Please try again."},
    {"type": "auth_done", "flowId": "f1", "providerId": "anthropic", "ok": True},
    {"type": "error", "code": "auth_flow_not_found", "message": "That sign-in is no longer active."},
])
def test_a_prompt_ended_by_the_daemon_while_typing_drops_the_secret(tmp_path: Path, ending: dict[str, Any]) -> None:
    async def body(pilot: Any, app: JarvisApp) -> bool:
        await _to_secret_prompt(pilot, app)
        await pilot.press(*SECRET)
        await LINK["emit"](ending)
        await wait_for(lambda: app._host.login.prompt is None, timeout=5.0)
        await pilot.pause()
        _no_secret(app)
        assert app.query_one("#prompt").password is False
        await pilot.press("enter")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("enter")
        await asyncio.sleep(0.2)
        _no_secret(app)
        return app._host.login.open

    still_open, sent = _run(tmp_path, _secret_prompt(), body)
    assert still_open is False
    assert _carrying_secret(sent) == []


def test_a_dropped_connection_ends_the_sign_in_and_no_stale_prompt_is_answered(tmp_path: Path) -> None:
    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _to_secret_prompt(pilot, app)
        await pilot.press(*SECRET[:6])
        first = LINK["socket"]
        await first.close()
        await wait_for(lambda: app._host.daemon_status.value != "connected", timeout=5.0)
        await pilot.pause()
        down = _screen(app)
        assert app.query_one("#prompt").value == ""
        await wait_for(lambda: LINK["socket"] is not first and app._host.daemon_status.value == "connected",
                       timeout=5.0)
        await pilot.pause()
        n_sent = len(sent_ref)
        await pilot.press(*SECRET, "enter")
        await asyncio.sleep(0.2)
        await pilot.pause()
        _no_secret(app)
        await pilot.press("escape")
        await pilot.pause()
        return {"down": down, "after": sent_ref[n_sent:], "prompt": app._host.login.prompt,
                "running": app._host.login.running, "open": app._host.login.open}

    # Run by hand: the body needs the frames sent so far, which ``_run`` only returns.
    sent_ref: list[dict[str, Any]] = []
    token = tmp_path / "token"
    token.write_text("daemon-token\n", encoding="utf-8")

    async def scenario() -> dict[str, Any]:
        async with fake_server(_backend) as port, fake_server(_daemon(sent_ref, _secret_prompt())) as dport:
            app = JarvisApp(url=f"ws://127.0.0.1:{dport}/ws", token_path=token,
                            backend_url=f"ws://127.0.0.1:{port}/api/v1/ws", backend_token="t", animate=False)
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(lambda: app._host.agent_status == "idle", timeout=5.0)
                await pilot.click("#prompt")
                return await body(pilot, app)

    out = asyncio.run(scenario())
    assert _has(out["down"], "The connection to Gentle Dot dropped: the sign-in ended.")
    assert not _has(out["down"], "Paste your Anthropic API key")
    assert out["prompt"] is None and out["running"] is False and out["open"] is False
    assert not any(m["type"] in ("auth_reply", "send") for m in out["after"])
    assert _carrying_secret(sent_ref) == []


def test_auth_flow_not_found_ends_the_flow_and_esc_closes(tmp_path: Path) -> None:
    async def script(message: dict[str, Any], emit: Emit) -> None:
        await _secret_prompt()(message, emit)
        if message["type"] == "auth_reply":
            await emit({"type": "error", "code": "auth_flow_not_found", "message": "That sign-in is no longer active."})

    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _to_secret_prompt(pilot, app)
        await _type(pilot, app, "typed-key")
        await wait_for(lambda: _has(_screen(app), "That sign-in is no longer active."), timeout=5.0)
        running = app._host.login.running
        await pilot.press("escape")
        await pilot.pause()
        return {"running": running, "open": app._host.login.open}

    out, sent = _run(tmp_path, script, body)
    assert out == {"running": False, "open": False}
    assert sum(1 for m in sent if m.get("cancelled")) == 0


def test_esc_before_the_daemon_names_the_flow_cancels_it_when_it_does(tmp_path: Path) -> None:
    async def script(message: dict[str, Any], emit: Emit) -> None:
        if message["type"] == "auth_reply" and message.get("cancelled"):
            await emit({"type": "auth_done", "flowId": message["flowId"], "providerId": "anthropic", "ok": False,
                        "message": "Sign-in cancelled."})

    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _type(pilot, app, "/login")
        await wait_for(lambda: _has(_screen(app), "Anthropic"), timeout=5.0)
        await _type(pilot, app, "1")  # the daemon stays quiet for now
        await pilot.press("escape")
        await pilot.pause()
        cancelling = _has(_screen(app), "Cancelling…")
        await LINK["emit"]({"type": "auth_prompt", "prompt": {"flowId": "f5", "kind": "secret", "message": "Key please"}})
        await wait_for(lambda: _has(_screen(app), "Sign-in cancelled."), timeout=5.0)
        return {"cancelling": cancelling, "asked": _has(_screen(app), "Key please")}

    out, sent = _run(tmp_path, script, body)
    assert out == {"cancelling": True, "asked": False}
    assert sent.count({"type": "auth_reply", "flowId": "f5", "cancelled": True}) == 1


def test_esc_closes_even_when_the_daemon_never_answers_the_cancel(tmp_path: Path) -> None:
    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _to_secret_prompt(pilot, app)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        return {"open": app._host.login.open, "running": app._host.login.running}

    out, sent = _run(tmp_path, _secret_prompt(), body)
    assert out == {"open": False, "running": False}
    assert sent.count({"type": "auth_reply", "flowId": "f1", "cancelled": True}) == 1


def test_frames_of_another_flow_are_ignored(tmp_path: Path) -> None:
    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _to_secret_prompt(pilot, app)
        emit = LINK["emit"]
        await emit({"type": "auth_event", "flowId": "f9", "event": {"kind": "progress", "message": "FOREIGN step"}})
        await emit({"type": "auth_prompt", "prompt": {"flowId": "f9", "kind": "text", "message": "FOREIGN ask"}})
        await emit({"type": "auth_done", "flowId": "f9", "providerId": "openai", "ok": True})
        await asyncio.sleep(0.2)
        await pilot.pause()
        login = app._host.login
        return {"screen": _screen(app), "flow": login.flow_id, "result": login.result,
                "kind": login.prompt.kind if login.prompt else None}

    out, _ = _run(tmp_path, _secret_prompt(), body)
    assert "FOREIGN" not in out["screen"]
    assert out["flow"] == "f1" and out["result"] is None and out["kind"] == "secret"


BUSY = "Another sign-in is in progress. Finish or cancel it first."


def test_an_abandoned_flow_named_during_a_new_sign_in_is_cancelled_and_login_works_again(tmp_path: Path) -> None:
    """Esc twice before the flow is named, then /login again: the old flow's first frame
    lands between the new ``auth_login`` and its ``auth_busy``."""

    state: dict[str, Any] = {"logins": 0, "old": False}

    async def script(message: dict[str, Any], emit: Emit) -> None:
        if message["type"] == "auth_login":
            state["logins"] += 1
            if state["logins"] == 1:
                state["old"] = True  # the daemon starts "old" but names it later
            elif state["old"]:
                await emit({"type": "auth_prompt", "prompt": {"flowId": "old", "kind": "secret", "message": "OLD key"}})
                await emit({"type": "error", "code": "auth_busy", "message": BUSY})
            else:
                await emit({"type": "auth_prompt", "prompt": {"flowId": "new", "kind": "secret", "message": "NEW key"}})
        elif message["type"] == "auth_reply" and message.get("cancelled") and message["flowId"] == "old":
            state["old"] = False
            await emit({"type": "auth_done", "flowId": "old", "providerId": "anthropic", "ok": False,
                        "message": "Sign-in cancelled."})

    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _type(pilot, app, "/login")
        await wait_for(lambda: _has(_screen(app), "Anthropic"), timeout=5.0)
        await _type(pilot, app, "1")
        await pilot.press("escape", "escape")
        await pilot.pause()
        await _type(pilot, app, "/login")
        await wait_for(lambda: _has(_screen(app), "Anthropic"), timeout=5.0)
        await _type(pilot, app, "1")
        await wait_for(lambda: not state["old"] and not app._host.login.running, timeout=5.0)
        await asyncio.sleep(0.2)
        await pilot.pause()
        busy = {"screen": _screen(app), "result": app._host.login.result}
        await _type(pilot, app, "1")  # the daemon is free again: this sign-in starts
        await wait_for(lambda: _has(_screen(app), "NEW key"), timeout=5.0)
        return {**busy, "cancel_next": app._host.login.cancel_next, "flow": app._host.login.flow_id}

    out, sent = _run(tmp_path, script, body)
    assert sent.count({"type": "auth_reply", "flowId": "old", "cancelled": True}) == 1
    assert "OLD key" not in out["screen"]
    assert out["result"] == (False, BUSY)  # the old flow's auth_done does not replace it
    assert out["cancel_next"] is False and out["flow"] == "new"


def test_a_hidden_secret_cannot_be_copied_or_cut(tmp_path: Path) -> None:
    async def body(pilot: Any, app: JarvisApp) -> dict[str, Any]:
        await _to_secret_prompt(pilot, app)
        await pilot.press(*SECRET)
        prompt = app.query_one("#prompt")
        prompt.select_all()
        await pilot.press("ctrl+c")
        await pilot.pause()
        copied = app.clipboard
        await pilot.press("ctrl+x")
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        return {"copied": copied, "clipboard": app.clipboard, "value": prompt.value}

    out, _ = _run(tmp_path, _secret_prompt(), body)
    assert SECRET not in out["copied"] and SECRET not in out["clipboard"]
    assert out["value"] == SECRET  # cut did not take it out either
