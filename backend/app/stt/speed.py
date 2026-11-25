"""Warn when STT is slow enough that whisper-server is running on the CPU (S5).

Measured with large-v3-turbo: ~14.85 s per turn on CPU against ~350 ms on a Vulkan
GPU. whisper.cpp 1.9.5 tells its device only on its own stderr at start-up
(``whisper_backend_init_gpu: no GPU found`` or ``using Vulkan0 backend``), which the
backend does not see: ``GET /health`` answers a constant ``{"status":"ok"}`` and
``/inference`` returns only the text. So the backend judges by the time it measures.
With a ~40x gap between the two, one threshold (``JARVIS_STT_SLOW_MS``, 3000 ms by
default) separates them without guessing.

Every slow turn carries the warning on ``turn_stage{stage:"stt"}`` (C2), so a TUI
that connects later still learns it; the log says it once per process.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Final

ENV_SLOW_MS: Final = "JARVIS_STT_SLOW_MS"
DEFAULT_SLOW_MS: Final = 3000.0
CPU_HINT: Final = "~15 s por turno en CPU vs ~0,35 s en GPU Vulkan"

logger = logging.getLogger("uvicorn.error")


class SttSpeedWatch:
    """Turns an STT time into a CPU warning; logs the first one only."""

    def __init__(self, slow_ms: float = DEFAULT_SLOW_MS) -> None:
        self.slow_ms = slow_ms
        self._logged = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SttSpeedWatch:
        env = os.environ if environ is None else environ
        return cls(float(env.get(ENV_SLOW_MS) or DEFAULT_SLOW_MS))

    def observe(self, stt_ms: float) -> str | None:
        """The warning for this STT time, or ``None`` when it is GPU-fast."""

        if stt_ms < self.slow_ms:
            return None
        warning = (
            f"STT lento ({stt_ms:.0f} ms): whisper-server parece correr en CPU "
            f"({CPU_HINT}). Revisar que arranque con GPU (sin -ng) y que whisper.cpp "
            "tenga backend Vulkan."
        )
        if not self._logged:
            self._logged = True
            logger.warning(warning)
        return warning
