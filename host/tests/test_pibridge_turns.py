"""S4 and S5: one Pi prompt per ``send``, in the frames and order the clients expect."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fake_pi import FakeLauncher, die, fail, hang, reject, reply

from jarvis.pibridge.turns import Bridge

T = TypeVar("T")
Frames = list[dict[str, Any]]


def run(coro: Awaitable[T]) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(coro, 10)

    return asyncio.run(bounded())


async def wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "condition was not met in time"
        await asyncio.sleep(0.01)


def make(launcher: FakeLauncher) -> tuple[Bridge, Frames]:
    frames: Frames = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    return Bridge(launcher, emit, model="openai/test-model"), frames


def idle_count(frames: Frames) -> int:
    return sum(1 for f in frames if f == {"type": "agent_state", "state": "idle"})


def kinds(frames: Frames) -> list[str]:
    return [f["type"] for f in frames]


def test_one_turn_streams_echo_deltas_done_and_idle_in_order() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("Hola, ", "soy ", "Jarvis."))
        bridge, frames = make(launcher)
        assert bridge.agent_state == "idle"
        await bridge.send("hola")
        await wait_for(lambda: idle_count(frames) == 1)
        assert kinds(frames) == [
            "agent_state",
            "user_message",
            "message_delta",
            "message_delta",
            "message_delta",
            "message_done",
            "agent_state",
        ]
        assert frames[0] == {"type": "agent_state", "state": "working"}
        echo = frames[1]
        assert echo["text"] == "hola"
        deltas = [f for f in frames if f["type"] == "message_delta"]
        done = frames[5]
        assert {d["messageId"] for d in deltas} == {done["messageId"]}
        assert done["messageId"] != echo["messageId"]
        assert "".join(d["delta"] for d in deltas) == done["text"] == "Hola, soy Jarvis."
        assert done.get("stopped") is not True and not done.get("error")
        assert bridge.agent_state == "idle"
        pi = launcher.current
        prompts = [r for r in pi.written if r["type"] == "prompt"]
        assert len(prompts) == 1 and prompts[0]["message"] == "hola" and prompts[0]["id"]
        assert len(launcher.launches) == 1
        await bridge.close()
        assert pi.closed

    run(scenario())


def test_a_pi_provider_error_ends_the_message_and_sends_an_error_frame() -> None:
    async def scenario() -> None:
        bridge, frames = make(FakeLauncher(fail("429 rate limited", "Ho"), reply("ok")))
        await bridge.send("hola")
        await wait_for(lambda: idle_count(frames) == 1)
        assert kinds(frames) == [
            "agent_state", "user_message", "message_delta", "message_done", "error", "agent_state",
        ]
        done, error = frames[3], frames[4]
        assert done["error"] == "429 rate limited"
        assert done["text"] == "Ho"
        assert error["code"] == "agent_error" and error["message"] == "429 rate limited"
        await bridge.send("otra")
        await wait_for(lambda: idle_count(frames) == 2)
        assert frames[-2]["type"] == "message_done" and frames[-2]["text"] == "ok"
        await bridge.close()

    run(scenario())


def test_a_rejected_prompt_is_an_error_without_an_echo() -> None:
    async def scenario() -> None:
        bridge, frames = make(FakeLauncher(reject("No API key for openai")))
        await bridge.send("hola")
        await wait_for(lambda: idle_count(frames) == 1)
        assert frames == [
            {"type": "agent_state", "state": "working"},
            {"type": "error", "code": "prompt_rejected", "message": "No API key for openai"},
            {"type": "agent_state", "state": "idle"},
        ]
        await bridge.close()

    run(scenario())


def test_abort_stops_the_turn_and_the_next_send_works() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(hang("Voy a "), reply("Listo."))
        bridge, frames = make(launcher)
        await bridge.send("tarea larga")
        await wait_for(lambda: any(f["type"] == "message_delta" for f in frames))
        await bridge.abort()
        await wait_for(lambda: idle_count(frames) == 1)
        pi = launcher.current
        assert [r["type"] for r in pi.written] == ["prompt", "abort"]
        done = [f for f in frames if f["type"] == "message_done"]
        assert len(done) == 1 and done[0]["stopped"] is True and not done[0].get("error")
        assert kinds(frames)[-2:] == ["message_done", "agent_state"]
        assert not any(f["type"] == "error" for f in frames)
        assert bridge.agent_state == "idle"
        await bridge.send("hola")
        await wait_for(lambda: idle_count(frames) == 2)
        assert frames[-2]["type"] == "message_done" and frames[-2]["text"] == "Listo."
        assert len(launcher.launches) == 1  # the same Pi answered after the abort
        await bridge.close()

    run(scenario())


def test_abort_with_nothing_running_does_nothing() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        bridge, frames = make(launcher)
        await bridge.start()
        await bridge.abort()
        await asyncio.sleep(0.05)
        assert frames == []
        assert launcher.current.written == []
        await bridge.close()

    run(scenario())


def test_a_send_during_a_turn_waits_in_the_queue_and_runs_next() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        launcher = FakeLauncher(reply("uno", gate=gate), reply("dos"))
        bridge, frames = make(launcher)
        await bridge.send("primera")
        await wait_for(lambda: any(f["type"] == "message_delta" for f in frames))
        await bridge.send("segunda")
        await asyncio.sleep(0.05)
        pi = launcher.current
        assert [r["message"] for r in pi.written if r["type"] == "prompt"] == ["primera"]
        assert frames[-1] == {"type": "queue", "steering": [], "followUp": ["segunda"]}
        assert bridge.agent_state == "working"
        gate.set()
        await wait_for(lambda: idle_count(frames) == 1)
        assert [r["message"] for r in pi.written if r["type"] == "prompt"] == ["primera", "segunda"]
        echoes = [f["text"] for f in frames if f["type"] == "user_message"]
        done = [f["text"] for f in frames if f["type"] == "message_done"]
        assert echoes == ["primera", "segunda"] and done == ["uno", "dos"]
        # The first turn's done comes before the second echo; one idle, at the very end.
        assert kinds(frames).index("message_done") < [
            i for i, f in enumerate(frames) if f["type"] == "user_message"
        ][1]
        assert {"type": "queue", "steering": [], "followUp": []} in frames
        assert frames[-1] == {"type": "agent_state", "state": "idle"}
        await bridge.close()

    run(scenario())


def test_abort_also_drops_the_queued_sends() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(hang("a"))
        bridge, frames = make(launcher)
        await bridge.send("primera")
        await wait_for(lambda: any(f["type"] == "message_delta" for f in frames))
        await bridge.send("segunda")
        await bridge.abort()
        await wait_for(lambda: idle_count(frames) == 1)
        await asyncio.sleep(0.05)
        assert [r["message"] for r in launcher.current.written if r["type"] == "prompt"] == ["primera"]
        assert frames[-1] == {"type": "agent_state", "state": "idle"}
        assert {"type": "queue", "steering": [], "followUp": []} in frames
        await bridge.close()

    run(scenario())


def test_pi_exiting_mid_turn_is_an_error_and_the_next_send_restarts_it() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(die("me caig", code=3), reply("de vuelta"))
        bridge, frames = make(launcher)
        await bridge.send("hola")
        await wait_for(lambda: idle_count(frames) == 1)
        assert kinds(frames) == [
            "agent_state", "user_message", "message_delta", "message_done", "error", "agent_state",
        ]
        done, error = frames[3], frames[4]
        assert done["text"] == "me caig" and done["error"]
        assert error["code"] == "agent_exited" and "3" in error["message"]
        first = launcher.launches[0]
        assert first.exit_code == 3
        assert len(launcher.launches) == 1  # no restart until someone asks
        await bridge.send("sigues?")
        await wait_for(lambda: idle_count(frames) == 2)
        assert len(launcher.launches) == 2
        assert [r["message"] for r in launcher.current.written if r["type"] == "prompt"] == ["sigues?"]
        assert frames[-2]["type"] == "message_done" and frames[-2]["text"] == "de vuelta"
        await bridge.close()

    run(scenario())


def test_pi_that_cannot_start_is_an_error_and_a_later_send_retries() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher(reply("ya"))
        attempts: list[int] = []

        async def flaky() -> Any:
            attempts.append(1)
            if len(attempts) == 1:
                raise FileNotFoundError("pi")
            return await launcher()

        frames: Frames = []

        async def emit(frame: dict[str, Any]) -> None:
            frames.append(frame)

        bridge = Bridge(flaky, emit, model="m")
        await bridge.send("hola")
        assert frames == [
            {"type": "agent_state", "state": "working"},
            {"type": "error", "code": "agent_unavailable", "message": frames[1]["message"]},
            {"type": "agent_state", "state": "idle"},
        ]
        await bridge.send("hola")
        await wait_for(lambda: idle_count(frames) == 2)
        assert frames[-2]["text"] == "ya"
        await bridge.close()

    run(scenario())


def test_the_echo_comes_first_even_when_pi_streams_before_its_response() -> None:
    async def early(pi: Any, record: dict[str, Any]) -> None:
        pi.emit({"type": "agent_start"})
        pi.emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
        pi.emit({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "ya"}})
        pi.respond(record, "prompt", data={"disposition": "started"})
        message = {"role": "assistant", "content": [{"type": "text", "text": "ya"}], "stopReason": "stop"}
        pi.emit({"type": "message_end", "message": message})
        pi.emit({"type": "agent_settled", "aborted": False})

    async def scenario() -> None:
        bridge, frames = make(FakeLauncher(early))
        await bridge.send("rápido")
        await wait_for(lambda: idle_count(frames) == 1)
        assert kinds(frames) == ["agent_state", "user_message", "message_delta", "message_done", "agent_state"]
        assert frames[3]["text"] == "ya"
        await bridge.close()

    run(scenario())
