"""STT on CPU warning (S5): whisper-server is ~40x slower on CPU than on a Vulkan GPU.

whisper-server 1.9.5 says which device it uses only on its own stderr ("no GPU found"
or "using Vulkan0 backend"); ``/health`` answers a constant ``{"status":"ok"}`` and
``/inference`` only the text. The backend therefore judges by the measured STT time.
"""

from __future__ import annotations

import logging

from app import main
from app.fakes import FakeTranscriber
from app.stt.speed import CPU_HINT, DEFAULT_SLOW_MS, ENV_SLOW_MS, SttSpeedWatch
from test_converse_stt import _turn_events

CPU_MS = 14850.0  # Jaime's measurement on CPU
GPU_MS = 350.0  # ~ on a Vulkan GPU


def test_a_cpu_sized_stt_time_warns_with_the_cost_hint():
    warning = SttSpeedWatch().observe(CPU_MS)
    assert warning is not None
    assert "CPU" in warning and "14850 ms" in warning
    assert CPU_HINT in warning
    assert CPU_HINT == "~15 s por turno en CPU vs ~0,35 s en GPU Vulkan"


def test_a_gpu_sized_stt_time_does_not_warn(caplog):
    watch = SttSpeedWatch()
    with caplog.at_level(logging.WARNING):
        assert watch.observe(GPU_MS) is None
    assert caplog.records == []


def test_the_log_warns_once_but_every_slow_turn_carries_the_warning(caplog):
    watch = SttSpeedWatch()
    with caplog.at_level(logging.WARNING):
        first = watch.observe(CPU_MS)
        second = watch.observe(CPU_MS + 100)
    assert first is not None and second is not None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert CPU_HINT in warnings[0].getMessage()


def test_threshold_comes_from_the_environment():
    assert SttSpeedWatch.from_env({}).slow_ms == DEFAULT_SLOW_MS
    watch = SttSpeedWatch.from_env({ENV_SLOW_MS: "8000"})
    assert watch.slow_ms == 8000.0
    assert watch.observe(5000.0) is None
    assert watch.observe(9000.0) is not None


def test_slow_stt_turn_stage_carries_the_warning(client, wav, monkeypatch):
    monkeypatch.setattr(main, "transcriber", FakeTranscriber("hola"))
    monkeypatch.setattr(main, "stt_watch", SttSpeedWatch(slow_ms=0.0))
    resp, events = _turn_events(client, wav)
    assert resp.status_code == 200
    (stage,) = [e for e in events if e["type"] == "turn_stage"]
    assert stage["stage"] == "stt"
    assert CPU_HINT in stage["warning"]


def test_fast_stt_turn_stage_has_no_warning(client, wav, monkeypatch):
    monkeypatch.setattr(main, "transcriber", FakeTranscriber("hola"))
    monkeypatch.setattr(main, "stt_watch", SttSpeedWatch())
    resp, events = _turn_events(client, wav)
    assert resp.status_code == 200
    (stage,) = [e for e in events if e["type"] == "turn_stage"]
    assert "warning" not in stage
