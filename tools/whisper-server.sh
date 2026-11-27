#!/usr/bin/env bash
# Start a local whisper.cpp server for the backend's STT stage (JARVIS_STT=whisper).
#
#   tools/whisper-server.sh [extra whisper-server flags, e.g. -t 8 or -ng]
#
# Environment (all optional):
#   JARVIS_WHISPER_MODEL  model file  (~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin)
#   JARVIS_WHISPER_VAD_MODEL  Silero VAD model (~/.local/share/whisper-models/ggml-silero-v5.1.2.bin)
#   JARVIS_WHISPER_HOST   bind host   (127.0.0.1)
#   JARVIS_WHISPER_PORT   port        (8080; the backend default JARVIS_STT_URL uses it)
#   JARVIS_STT_LANGUAGE   language    (es)
#   JARVIS_WHISPER_BIN    binary      (whisper-server)
#
# Runs in the foreground; stop it with Ctrl-C. The backend sends WAV 16 kHz mono
# 16-bit (D6), which whisper-server reads without --convert.
#
# VAD and --suppress-nst are always on: without them the model turns silence or
# room noise into text (" Gracias."). Get the VAD model once with:
#   curl -L -o ~/.local/share/whisper-models/ggml-silero-v5.1.2.bin \
#     https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin
set -euo pipefail

model="${JARVIS_WHISPER_MODEL:-$HOME/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin}"
host="${JARVIS_WHISPER_HOST:-127.0.0.1}"
port="${JARVIS_WHISPER_PORT:-8080}"
vad_model="${JARVIS_WHISPER_VAD_MODEL:-$HOME/.local/share/whisper-models/ggml-silero-v5.1.2.bin}"
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
if [[ ! -f "$vad_model" ]]; then
  echo "error: VAD model not found: $vad_model (see the header of this script, or set JARVIS_WHISPER_VAD_MODEL)" >&2
  exit 1
fi

echo "whisper-server: $model, language $language, http://$host:$port/inference" >&2
exec "$bin" -m "$model" -l "$language" --host "$host" --port "$port" \
  --vad --vad-model "$vad_model" --suppress-nst "$@"
