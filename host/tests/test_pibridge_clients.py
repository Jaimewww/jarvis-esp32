"""S9: several clients, the conversation so far, a ready account and an answer to every request."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fake_pi import FakeLauncher, reply, tool_turn
from pibridge_harness import bridge_at, jarvis, raw, reply_to, run, wait_for

from jarvis.client import (
    AgentStateEvent,
    AuthProvidersEvent,
    ConversationsEvent,
    ErrorEvent,
    HistoryEvent,
    MessageDoneEvent,
    Ready,
    UserMessageEvent,
)


def test_two_clients_both_receive_the_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("Son ", "las cinco."))
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as first, jarvis(running) as second:
                await wait_for(lambda: running.hub.size == 2)
                assert first.client.send("qué hora es")
                for probe in (first, second):
                    await wait_for(lambda p=probe: any(  # type: ignore[misc]
                        isinstance(e, AgentStateEvent) and e.state == "idle" for e in p.events))
                    assert [e.text for e in probe.of(UserMessageEvent)] == ["qué hora es"]
                    assert [e.text for e in probe.of(MessageDoneEvent)] == ["Son las cinco."]
                    assert [m.text for m in probe.host.messages] == ["qué hora es", "Son las cinco."]
                    assert not probe.of(ErrorEvent)

    run(scenario())


def test_get_history_returns_the_conversation_so_far_with_its_activities(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("Hola."), tool_turn("pc_media", final="Subí el volumen."))
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as first:
                for text in ("hola", "sube el volumen"):
                    count = len(first.of(MessageDoneEvent))
                    assert first.client.send(text)
                    await wait_for(lambda c=count: len(first.of(MessageDoneEvent)) > c and first.host.agent_state == "idle")  # type: ignore[misc]
            # A client that connects later asks get_history right after ready.
            async with jarvis(running) as late:
                await wait_for(lambda: bool(late.of(HistoryEvent)))
                (history,) = late.of(HistoryEvent)
                (ready,) = late.of(Ready)
                assert history.conversation_id == ready.conversation_id is not None
                assert not history.has_earlier
                roles_texts = [(m.role, m.text) for m in history.messages]
                assert roles_texts == [
                    ("user", "hola"), ("assistant", "Hola."),
                    ("user", "sube el volumen"), ("assistant", ""), ("assistant", "Subí el volumen."),
                ]
                tool_message = history.messages[3]
                assert [(a.id, a.kind, a.status) for a in tool_message.activities] == [("call_1", "pc_media", "done")]
                assert all(not m.activities for i, m in enumerate(history.messages) if i != 3)
                assert [m.text for m in late.host.messages] == [t for _, t in roles_texts]
                assert [a.status for a in late.host.activities_of(tool_message.id)] == ["done"]

    run(scenario())


def test_list_conversations_returns_the_one_conversation(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("Hola."))
        async with bridge_at(launcher, tmp_path / "token") as running:
            async with jarvis(running) as probe:
                await wait_for(lambda: bool(probe.of(ConversationsEvent)))
                (ready,) = probe.of(Ready)
                (listing,) = probe.of(ConversationsEvent)
                assert [c.id for c in listing.conversations] == [ready.conversation_id]
                assert listing.active_id == ready.conversation_id
                assert listing.conversations[0].title and listing.conversations[0].updated_at
                assert probe.host.chat_title == listing.conversations[0].title

    run(scenario())


def test_the_account_frames_make_the_client_consider_the_account_ready(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with bridge_at(FakeLauncher(), tmp_path / "token") as running, jarvis(running) as probe:
            await wait_for(lambda: bool(probe.of(AuthProvidersEvent)))
            (accounts,) = probe.of(AuthProvidersEvent)
            assert not accounts.open
            assert [p.configured for p in accounts.providers] == [True]
            assert accounts.providers[0].id == "openai"
            login = probe.host.login
            assert login.listed and not login.open and not login.running
            assert probe.host.agent_notice is None
            await asyncio.sleep(0.1)
            assert not probe.of(ErrorEvent)  # nothing the client asks after ready is refused

    run(scenario())


def test_an_unsupported_request_gets_an_error_answer_only_for_its_sender(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with bridge_at(FakeLauncher(), tmp_path / "token") as running:
            async with raw(running) as asker, raw(running) as other:
                steer = await reply_to(asker, {"type": "steer", "text": "mejor no"}, "error")
                assert steer["type"] == "error" and steer["code"] == "unsupported" and steer["message"]
                unknown = await reply_to(asker, {"type": "teleport"}, "error")
                assert unknown["code"] == "unsupported"
                login = await reply_to(asker, {"type": "auth_login", "providerId": "openai", "method": "oauth"}, "error")
                assert login["code"] == "auth_unsupported"
                for request in ({"type": "auth_reply", "flowId": "f1", "value": "x"},
                                {"type": "auth_logout", "providerId": "openai"}):
                    assert (await reply_to(asker, request, "error"))["code"] == "auth_unsupported"
                garbage = await reply_to(asker, {"type": None}, "error")
                assert garbage["code"] == "unsupported"
                await asker.send("not json")
                assert json.loads(await asyncio.wait_for(asker.recv(), 3))["code"] == "bad_request"
                with_seq = await reply_to(asker, {"type": "teleport"}, "error")
                assert with_seq["seq"] == 9  # ready was 1, then eight answers: numbered like any frame
                try:
                    stray = await asyncio.wait_for(other.recv(), 0.2)
                except TimeoutError:
                    stray = None
                assert stray is None  # another client's error is not broadcast

    run(scenario())
