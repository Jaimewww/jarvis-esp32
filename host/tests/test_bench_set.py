"""``python -m jarvis.bench`` fixed request set: selection, statistics, dry run, JSON, safety."""

from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path

import pytest
from conftest import fake_server
from fake_gentle_dot import FakeDaemon

from jarvis import bench
from jarvis.client import Ask
from jarvis.turntrace import ModelCall, ToolCall, TurnTrace, Wait


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


def _with_tools(trace: TurnTrace, *statuses: str) -> TurnTrace:
    trace.tools = [ToolCall(f"t{i}", "other", 0, 0.0, 0.1, status) for i, status in enumerate(statuses)]
    return trace


def test_a_run_is_done_only_without_error_failed_tool_or_missing_desktop_tool() -> None:
    assert bench.run_problem(_with_tools(_trace(3.0, 2), "done"), desktop_task=True) is None
    assert bench.run_problem(_trace(3.0, 1), desktop_task=False) is None  # a question needs no tool
    assert bench.run_problem(_with_tools(_trace(3.0, 2), "done", "failed"), desktop_task=True) == "tool failed"
    assert bench.run_problem(_with_tools(_trace(3.0, 2), "failed"), desktop_task=False) == "tool failed"
    assert bench.run_problem(_trace(3.0, 1), desktop_task=True) == "no tool"
    assert bench.run_problem(_trace(3.0, 1, error="boom"), desktop_task=True) == "error"


def test_stats_count_done_runs_and_keep_timing_the_rest() -> None:
    traces = [_with_tools(_trace(3.0, 2), "done"), _with_tools(_trace(4.0, 2), "failed"), _trace(5.0, 1)]
    stats = bench.summarize(traces, desktop_task=True)
    assert stats["runs"] == 3 and stats["done"] == 1 and stats["errors"] == 0
    assert stats["not_done"] == {"tool failed": 1, "no tool": 1}
    assert stats["total_s"] == {"median": 4.0, "worst": 5.0}  # failed runs still timed
    assert bench.summarize(traces[:1] + traces[2:])["done"] == 2  # a question: no tool is fine


def test_a_desktop_request_that_used_no_tool_is_not_done(tmp_path: Path) -> None:
    daemon = FakeDaemon(tool_call=True)  # model calls, but no tool activity
    out_json = tmp_path / "b.json"
    code, out, err = _run(tmp_path, daemon, "-n", "2", "--only", "spotify", "--only", "hora",
                          "--include-pc", "--json", str(out_json))
    assert code == 0, err
    assert "not done: no tool" in out
    header = next(line for line in out.splitlines() if line.startswith("request"))
    assert "done" in header.split()
    assert re.search(r"^spotify .* 0/2\s+0$", out, re.M)
    assert re.search(r"^hora .* 2/2\s+0$", out, re.M)
    spotify, hora = json.loads(out_json.read_text(encoding="utf-8"))["requests"]
    assert spotify["done"] == 0 and spotify["not_done"] == {"no tool": 2}
    assert [r["done"] for r in spotify["runs"]] == [False, False]
    assert spotify["runs"][0]["not_done"] == "no tool" and spotify["total_s"]["median"] > 0
    assert hora["done"] == 2 and hora["runs"][0]["done"] is True and hora["runs"][0]["not_done"] is None


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
