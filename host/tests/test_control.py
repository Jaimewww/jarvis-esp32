"""The control client (S13): ``jarvis-input``'s ``control.sock`` is the only source of truth.

The helper is the scripted ``FakeHelper`` on a temp ``XDG_RUNTIME_DIR`` (``conftest``);
this window writes no permission file anymore.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import wait_for
from fake_jarvis_input import FakeHelper

from jarvis.control import (
    CONTROL_MINUTES,
    ConfirmRequest,
    ControlError,
    ControlLink,
    ControlState,
    ControlUnavailable,
    control_socket_path,
    grant,
    parse_control,
    remaining_label,
    revoke,
    status,
)

NOW = datetime(2026, 11, 20, 15, 0, 0, tzinfo=UTC)


def test_socket_is_under_xdg_runtime_dir(tmp_path: Path) -> None:
    assert control_socket_path({"XDG_RUNTIME_DIR": str(tmp_path)}) == tmp_path / "jarvis-control" / "control.sock"


@pytest.mark.parametrize("environ", [{}, {"XDG_RUNTIME_DIR": ""}, {"XDG_RUNTIME_DIR": "relative/dir"}])
def test_socket_refuses_without_a_runtime_dir(environ: dict[str, str]) -> None:
    with pytest.raises(ControlError, match="XDG_RUNTIME_DIR"):
        control_socket_path(environ)


def test_grant_status_revoke_follow_the_helper(_private_runtime_dir: Path) -> None:
    async def scenario() -> dict[str, Any]:
        async with FakeHelper(_private_runtime_dir, clock=lambda: NOW) as helper:
            before = await status()
            granted = await grant()
            during = await status()
            await revoke()
            after = await status()
            return {"ops": list(helper.received), "before": before,
                    "granted": granted, "during": during, "after": after}

    out = asyncio.run(scenario())
    assert CONTROL_MINUTES == 30
    assert out["ops"] == [{"op": "status"}, {"op": "grant", "minutes": 30}, {"op": "status"},
                          {"op": "revoke"}, {"op": "status"}]
    assert out["before"] == ControlState(False, None)
    assert out["granted"] == ControlState(True, NOW + timedelta(minutes=30))
    assert out["during"] == out["granted"]
    assert out["granted"].remaining_s(NOW + timedelta(seconds=48)) == 29 * 60 + 12
    assert out["after"] == ControlState(False, None)
    # No permission file: only the helper's socket lives in the runtime dir.
    assert sorted(p.name for p in _private_runtime_dir.iterdir()) == ["jarvis-control"]
    assert list((_private_runtime_dir / "jarvis-control").iterdir()) == []


@pytest.mark.parametrize("minutes", [0, 31, -5])
def test_grant_refuses_more_than_30_minutes(minutes: int) -> None:
    with pytest.raises(ValueError):
        asyncio.run(grant(minutes))


def test_helper_not_running_is_a_clear_error(_private_runtime_dir: Path) -> None:
    with pytest.raises(ControlUnavailable, match="^jarvis-input is not running: "):
        asyncio.run(grant())
    # A stale socket file with nobody listening is the same.
    stale = _private_runtime_dir / "jarvis-control"
    stale.mkdir()
    (stale / "control.sock").write_text("", encoding="utf-8")
    with pytest.raises(ControlUnavailable, match="^jarvis-input is not running: "):
        asyncio.run(status())


def test_helper_refusal_is_reported(_private_runtime_dir: Path) -> None:
    async def scenario() -> None:
        async with FakeHelper(_private_runtime_dir) as helper:
            helper.refuse = "not allowed"
            await grant()

    with pytest.raises(ControlError, match="not allowed") as caught:
        asyncio.run(scenario())
    assert not isinstance(caught.value, ControlUnavailable)


class Recorder:
    def __init__(self) -> None:
        self.states: list[ControlState] = []
        self.confirms: list[ConfirmRequest] = []
        self.links: list[tuple[bool, str]] = []

    def link(self, **kwargs: Any) -> ControlLink:
        return ControlLink(on_state=self.states.append, on_confirm=self.confirms.append,
                           on_connection=lambda up, detail: self.links.append((up, detail)),
                           backoff=(0.05, 0.1), **kwargs)


@pytest.mark.parametrize("ack", [False, True])
def test_subscribe_delivers_state_and_confirm_and_answers_on_the_same_connection(
    _private_runtime_dir: Path, ack: bool
) -> None:
    seen = Recorder()

    async def scenario() -> None:
        async with FakeHelper(_private_runtime_dir, clock=lambda: NOW, ack_subscribe=ack) as helper:
            link = seen.link()
            task = asyncio.create_task(link.run())
            await wait_for(lambda: bool(helper.subscribers) and link.connected)
            await wait_for(lambda: bool(seen.states))  # the state at connect time
            await grant()
            await wait_for(lambda: any(s.enabled for s in seen.states))
            await helper.push_confirm(id="c7", timeout_s=12)
            await wait_for(lambda: bool(seen.confirms))
            assert link.answer("c7", True) is True
            await wait_for(lambda: bool(helper.answers))
            await link.close()
            await asyncio.wait_for(task, 2.0)
            assert helper.answers == [{"op": "answer", "id": "c7", "allow": True}]
            assert helper.ops("subscribe") == [{"op": "subscribe"}]

    asyncio.run(scenario())
    assert seen.states[0] == ControlState(False, None)
    assert ControlState(True, NOW + timedelta(minutes=30)) in seen.states
    assert seen.confirms == [ConfirmRequest("c7", "Press Return in Brave?", "pc_key Return in brave-browser", 12.0)]
    assert seen.links[0] == (True, "")


def test_link_reconnects_with_backoff_when_the_helper_restarts(_private_runtime_dir: Path) -> None:
    seen = Recorder()

    async def scenario() -> None:
        link = seen.link()
        task = asyncio.create_task(link.run())
        await asyncio.sleep(0.2)  # no helper yet: retried quietly
        assert not link.connected and link.answer("x", True) is False
        helper = FakeHelper(_private_runtime_dir, clock=lambda: NOW)
        await helper.start()
        await wait_for(lambda: link.connected)
        await grant()
        await wait_for(lambda: any(s.enabled for s in seen.states))
        await helper.stop()  # a restart revokes; the link notices
        await wait_for(lambda: not link.connected)
        assert link.answer("x", False) is False
        await helper.start()
        await wait_for(lambda: link.connected and len(helper.subscribers) == 1)
        await wait_for(lambda: not seen.states[-1].enabled)
        await link.close()
        await asyncio.wait_for(task, 2.0)
        await helper.stop()

    asyncio.run(scenario())
    ups = [up for up, _ in seen.links]
    assert ups == [False, True, False, True]  # transitions only, not every retry
    assert any(not up and "jarvis-input is not running" in detail for up, detail in seen.links)


def test_malformed_events_are_ignored(_private_runtime_dir: Path) -> None:
    seen = Recorder()

    async def scenario() -> None:
        async with FakeHelper(_private_runtime_dir) as helper:
            link = seen.link()
            task = asyncio.create_task(link.run())
            await wait_for(lambda: link.connected and bool(helper.subscribers))
            writer = helper.subscribers[0]
            writer.write(b"{not json\n")
            writer.write(b'{"event":"confirm","title":"no id"}\n')
            writer.write(b'{"event":"state","enabled":true,"expires_at":"soon"}\n')
            writer.write(b'{"event":"mystery"}\n')
            await helper.push_confirm(id="ok")
            await wait_for(lambda: bool(seen.confirms))
            await link.close()
            await asyncio.wait_for(task, 2.0)

    asyncio.run(scenario())
    assert [c.id for c in seen.confirms] == ["ok"]
    assert all(s.enabled is False for s in seen.states)  # "soon" is no expiry: off


def test_remaining_label() -> None:
    assert remaining_label(29 * 60 + 12) == "29:12"
    assert remaining_label(59.4) == "0:59"
    assert remaining_label(-3) == "0:00"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/control on", "on"),
        ("  /CONTROL   Off ", "off"),
        ("/control status", "status"),
        ("/control", "status"),
        ("/control maybe", "usage"),
        ("/controlled", None),
        ("abre /control on", None),
        ("hola", None),
    ],
)
def test_parse_control(text: str, expected: str | None) -> None:
    assert parse_control(text) == expected
