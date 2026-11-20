#!/usr/bin/env bash
# Start a local whisper.cpp server for the backend's STT stage (JARVIS_STT=whisper).
#
#   tools/whisper-server.sh [extra whisper-server flags, e.g. -t 8 or -ng]
#
# Environment (all optional):
#   JARVIS_WHISPER_MODEL  model file  (~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin)
#   JARVIS_WHISPER_HOST   bind host   (127.0.0.1)
#   JARVIS_WHISPER_PORT   port        (8080; the backend default JARVIS_STT_URL uses it)
#   JARVIS_STT_LANGUAGE   language    (es)
#   JARVIS_WHISPER_BIN    binary      (whisper-server)
#
# Runs in the foreground; stop it with Ctrl-C. The backend sends WAV 16 kHz mono
# 16-bit (D6), which whisper-server reads without --convert.
set -euo pipefail

model="${JARVIS_WHISPER_MODEL:-$HOME/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin}"
host="${JARVIS_WHISPER_HOST:-127.0.0.1}"
port="${JARVIS_WHISPER_PORT:-8080}"
language="${JARVIS_STT_LANGUAGE:-es}"
bin="${JARVIS_WHISPER_BIN:-whisper-server}"

if ! command -v "$bin" >/dev/null 2>&1; then
  echo "error: '$bin' not found (Arch: package whisper-cpp, or set JARVIS_WHISPER_BIN)" >&2
  exit 1
fi
if [[ ! -f "$model" ]]; then
  echo "error: model not found: $model (set JARVIS_WHISPER_MODEL)" >&2
  exit 1
fi

echo "whisper-server: $model, language $language, http://$host:$port/inference" >&2
exec "$bin" -m "$model" -l "$language" --host "$host" --port "$port" "$@"
