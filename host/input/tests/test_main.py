"""`python3 -m jarvis_input` as a process: refuses a bad runtime dir, serves and exits on SIGTERM."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def spawn(env_extra: dict[str, str | None]) -> subprocess.Popen[bytes]:
    env = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
    env["PATH"] = "/nonexistent"  # never reach a real wtype/grim
    for k, v in env_extra.items():
        if v is not None:
            env[k] = v
    return subprocess.Popen([sys.executable, "-m", "jarvis_input"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class MainTest(unittest.TestCase):
    def test_refuses_without_runtime_dir(self) -> None:
        for value in [None, "relative/dir"]:
            p = spawn({"XDG_RUNTIME_DIR": value})
            _, err = p.communicate(timeout=10)
            self.assertEqual(p.returncode, 2, err)
            self.assertIn(b"XDG_RUNTIME_DIR", err)

    def test_serves_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = spawn({"XDG_RUNTIME_DIR": tmp})
            try:
                sock = Path(tmp) / "jarvis-control" / "control.sock"
                for _ in range(100):
                    if sock.exists():
                        break
                    time.sleep(0.05)
                s = socket.socket(socket.AF_UNIX)
                s.connect(str(sock))
                s.sendall(b'{"op":"status"}\n')
                self.assertEqual(json.loads(s.makefile().readline()),
                                 {"ok": True, "enabled": False, "expires_at": None})
                s.close()
            finally:
                p.send_signal(signal.SIGTERM)
                p.communicate(timeout=10)
            self.assertEqual(p.returncode, 0)
            self.assertFalse(sock.exists())


if __name__ == "__main__":
    unittest.main()
