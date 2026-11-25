#!/usr/bin/env bash
# Smoke-test a local Piper voice with the fixed reply used by /converse.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo 'Usage: bash tools/probe-piper.sh VOICE.onnx OUTPUT.wav' >&2
  exit 2
fi

python_bin=${PYTHON:-python}
model=$1
output=$2
scratch=$(mktemp -d)
trap 'rm -f -- "$scratch/piper.wav"; rmdir -- "$scratch"' EXIT

"$python_bin" -m piper -m "$model" -f "$scratch/piper.wav" -- 'En orden, señor.'
ffmpeg -hide_banner -loglevel error -nostdin -n -i "$scratch/piper.wav" \
  -ar 16000 -ac 1 -c:a pcm_s16le "$output"

"$python_bin" - "$output" <<'PY'
import sys
import wave

with wave.open(sys.argv[1], "rb") as audio:
    actual = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth())
    if actual != (16000, 1, 2):
        raise SystemExit(f"Unexpected WAV format: {actual}")
    frames = audio.getnframes()
    if not any(audio.readframes(frames)):
        raise SystemExit("Piper produced no audible samples")
print(f"{sys.argv[1]}: PCM 16 kHz, mono, 16-bit; {frames / 16000:.2f} s of audio")
PY
