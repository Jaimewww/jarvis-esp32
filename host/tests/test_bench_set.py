"""``python -m jarvis.bench`` fixed request set: selection, statistics, dry run, JSON, safety."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from conftest import fake_server
from fake_gentle_dot import FakeDaemon

from jarvis import bench
from jarvis.client import Ask
from jarvis.turntrace import ModelCall, TurnTrace, Wait


def _run(tmp_path: Path, daemon: FakeDaemon | None, *args: str, ask_human: bench.AskHuman | None = None) -> tuple[int, str, str]:
    token_file = tmp_path / "token"
    token_file.write_text("t\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()

    async def scenario() -> int:
        if daemon is None:  # nothing listens on port 9
            argv = ["--url", "ws://127.0.0.1:9/ws", "--token-file", str(token_file), *args]
            return await bench.run(argv, out, err, ask_human=ask_human)
        async with fake_server(daemon.handler) as port:
            argv = ["--url", f"ws://127.0.0.1:{port}/ws", "--token-file", str(token_file), *args]
            return await bench.run(argv, out, err, ask_human=ask_human)

    code = asyncio.run(scenario())
    return code, out.getvalue(), err.getvalue()


def _trace(total: float, calls: int, wait: float = 0.0, error: str | None = None) -> TurnTrace:
    trace = TurnTrace(0.0, 0.0)
    trace.calls = [ModelCall(f"a{i}", 0.0, end=0.1) for i in range(calls)]
    trace.waits = [Wait("k", 0.0, wait)] if wait else []
    trace.finish(total, "error" if error else "idle")
    trace.error = error
    return trace


def test_the_repo_set_has_10_to_15_requests_and_3_pc_tasks() -> None:
    requests = bench.load_requests()
    assert 10 <= len(requests) <= 15
    assert len({r.id for r in requests}) == len(requests)
    kinds = [r.kind for r in requests]
    assert kinds.count("pc") == 3 and "catalog" in kinds and "question" in kinds
    assert all(r.desktop for r in requests if r.kind in ("pc", "catalog"))


def test_desktop_requests_are_off_unless_include_pc() -> None:
    requests = bench.load_requests()
    default = bench.select(requests)
    assert default and not any(r.desktop for r in default)
    assert bench.select(requests, include_pc=True) == requests
    assert [r.id for r in bench.select(requests, only=["hora", "question"])][0] == "hora"
    assert {r.kind for r in bench.select(requests, only=["catalog"], include_pc=True)} == {"catalog"}
    with pytest.raises(ValueError, match="--include-pc"):
        bench.select(requests, only=["spotify"])
    with pytest.raises(ValueError, match="unknown"):
        bench.select(requests, only=["nope"])


def test_stats_report_median_and_worst_of_total_and_calls() -> None:
    stats = bench.summarize([_trace(3.0, 1), _trace(5.0, 3, wait=2.0), _trace(4.0, 2), _trace(9.0, 1, error="boom")])
    assert stats["runs"] == 4 and stats["errors"] == 1
    assert stats["total_s"] == {"median": 4.5, "worst": 9.0}
    assert stats["model_calls"] == {"median": 1.5, "worst": 3}
    assert stats["human_wait_s"] == {"median": 0.0, "worst": 2.0}
    assert stats["active_s"] == {"median": 3.5, "worst": 9.0}  # total minus a person's wait
    assert bench.summarize([])["total_s"] == {"median": None, "worst": None}


def test_dry_run_lists_the_set_and_connects_to_nothing(tmp_path: Path) -> None:
    code, out, err = _run(tmp_path, None, "--dry-run")
    assert code == 0, err
    assert "hora" in out and "¿Qué hora es?" in out
    assert "spotify" in out and "skipped" in out  # listed, but off without --include-pc
    code, out, _ = _run(tmp_path, None, "--dry-run", "--include-pc")
    assert "skipped" not in out and "pc-vscode" in out


def test_the_set_runs_each_request_n_times_and_writes_json(tmp_path: Path) -> None:
    daemon = FakeDaemon(delays=(0.0, 0.02, 0.02), tool_call=True)
    out_json = tmp_path / "bench.json"
    code, out, err = _run(tmp_path, daemon, "-n", "2", "--only", "hora", "--only", "dia", "--json", str(out_json))
    assert code == 0, err
    sends = [m["text"] for m in daemon.received if m["type"] == "send"]
    assert sends == ["¿Qué hora es?"] * 2 + ["¿Qué día es hoy?"] * 2
    assert "hora" in out and "median" in out and "worst" in out
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["runs"] == 2 and data["model"] == "openai/gpt-5.4" and data["include_pc"] is False
    [hora, dia] = data["requests"]
    assert hora["id"] == "hora" and len(hora["runs"]) == 2
    assert hora["model_calls"] == {"median": 2, "worst": 2}  # the tool call, then the text
    assert hora["total_s"]["worst"] >= hora["total_s"]["median"] > 0.03
    assert hora["runs"][0]["ended_by"] == "idle"


def test_a_confirmation_outside_include_pc_is_denied_without_asking(tmp_path: Path) -> None:
    asked: list[Ask] = []

    async def human(ask: Ask) -> bool:
        asked.append(ask)
        return True

    daemon = FakeDaemon(confirm=True)
    code, out, err = _run(tmp_path, daemon, "-n", "1", "--only", "hora", ask_human=human)
    assert code == 0, err
    assert asked == []
    assert {"type": "ui_response", "requestId": "r1", "confirmed": False} in daemon.received
    assert "denied" in out


def test_with_include_pc_a_person_answers_and_the_wait_is_timed(tmp_path: Path) -> None:
    async def human(ask: Ask) -> bool:
        await asyncio.sleep(0.2)
        return True

    daemon = FakeDaemon(confirm=True)
    out_json = tmp_path / "b.json"
    code, _, err = _run(tmp_path, daemon, "-n", "1", "--only", "hora", "--include-pc",
                        "--json", str(out_json), ask_human=human)
    assert code == 0, err
    assert {"type": "ui_response", "requestId": "r1", "confirmed": True} in daemon.received
    [hora] = json.loads(out_json.read_text(encoding="utf-8"))["requests"]
    assert hora["human_wait_s"]["median"] >= 0.15
    assert hora["active_s"]["median"] < hora["total_s"]["median"]
