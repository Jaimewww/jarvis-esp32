#!/bin/sh
# Desktop E2E for PC control (see host/input/README.md, "Desktop E2E").
# Exit: 0 all passed, 1 a case failed, 77 skipped (requirements missing).
cd "$(dirname "$0")/../input" && exec python3 -m jarvis_e2e "$@"
