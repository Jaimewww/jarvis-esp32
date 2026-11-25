"""``python -m app.stt`` CLI: ``--runs`` against a local fake whisper-server."""

from __future__ import annotations

import http.server
import json
import sys
import threading

import pytest

from app.stt.__main__ import main
from conftest import make_wav


@pytest.fixture
def fake_server(monkeypatch):
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 (http.server API)
            self.rfile.read(int(self.headers["Content-Length"]))
            hits.append(self.path)
            body = json.dumps({"text": " hola"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("JARVIS_STT_URL", f"http://127.0.0.1:{server.server_port}/inference")
    monkeypatch.setenv("JARVIS_STT_TIMEOUT_S", "5")
    yield hits
    server.shutdown()
    server.server_close()


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.wav"
    path.write_bytes(make_wav())
    return str(path)


def cli(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["python -m app.stt", *args])
    return main()


@pytest.mark.parametrize("runs", ["0", "-2"])
def test_runs_of_zero_or_less_is_a_usage_error(monkeypatch, capsys, fake_server, clip, runs):
    with pytest.raises(SystemExit) as exit_info:
        cli(monkeypatch, clip, "--runs", runs)
    assert exit_info.value.code == 2
    assert "--runs" in capsys.readouterr().err
    assert fake_server == []


def test_runs_defaults_to_one(monkeypatch, capsys, fake_server, clip):
    assert cli(monkeypatch, clip) == 0
    assert len(fake_server) == 1
    assert "run 1: stt" in capsys.readouterr().out


def test_runs_sends_each_file_that_many_times(monkeypatch, capsys, fake_server, clip):
    assert cli(monkeypatch, clip, "--runs", "3") == 0
    assert len(fake_server) == 3
    assert "run 3: stt" in capsys.readouterr().out
