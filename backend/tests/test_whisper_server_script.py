"""``tools/whisper-server.sh``: refuses to start without the VAD model, else always passes VAD flags."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "whisper-server.sh"


def _run(tmp_path: Path, *extra: str, vad: bool) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    argv_log = tmp_path / "argv"
    stub = tmp_path / "whisper-server-stub"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" >"{argv_log}"\n')
    stub.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    vad_model = tmp_path / "vad.bin"
    if vad:
        vad_model.write_bytes(b"v")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "JARVIS_WHISPER_BIN": str(stub),
        "JARVIS_WHISPER_MODEL": str(model),
        "JARVIS_WHISPER_VAD_MODEL": str(vad_model),
    }
    done = subprocess.run(["bash", str(SCRIPT), *extra], env=env, capture_output=True, text=True, timeout=10)
    return done, argv_log, vad_model


def test_missing_vad_model_exits_1_and_never_runs_the_server(tmp_path: Path) -> None:
    done, argv_log, _ = _run(tmp_path, vad=False)
    assert done.returncode == 1
    assert "VAD model not found" in done.stderr
    assert not argv_log.exists()


def test_vad_flags_are_always_passed_before_the_users_extra_flags(tmp_path: Path) -> None:
    done, argv_log, vad_model = _run(tmp_path, "-t", "8", vad=True)
    assert done.returncode == 0, done.stderr
    argv = argv_log.read_text().splitlines()
    vad = argv.index("--vad")
    assert argv[argv.index("--vad-model") + 1] == str(vad_model)
    nst = argv.index("--suppress-nst")
    assert argv[-2:] == ["-t", "8"]
    assert max(vad, argv.index("--vad-model"), nst) < len(argv) - 2
