#!/usr/bin/env bash
# Self-contained checks for host/bin/jarvis-stop.
#
#   bash host/bin/test_jarvis_stop.sh
#
# Runs in a temporary sandbox: XDG_RUNTIME_DIR and GENTLE_DOT_DATA_DIR point into it, `pkill` and
# `notify-send` are stubs (no real process is touched; the script fixes its own PATH, so the cases run
# a copy whose PATH line lists the stub directory first, while the caller's PATH holds decoys that
# must never run), a fake jarvis-input control socket
# (Python stdlib, AF_UNIX) and a fake daemon (Python stdlib WebSocket server on a random 127.0.0.1
# port) record what the script sends. JARVIS_STOP_BIN overrides the script under test.
set -euo pipefail

readonly HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly STOP_REAL="${JARVIS_STOP_BIN:-${HERE}/jarvis-stop}"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-stop-test.XXXXXX")"
readonly SANDBOX
# The script fixes its own PATH (S15), so stubs on the caller's PATH would be ignored: the
# behavior cases run a copy whose PATH line also lists the stub directory first.
readonly STOP="${SANDBOX}/jarvis-stop"
SERVER_PID="" CONTROL_PID=""
cleanup() {
	[[ -z "${SERVER_PID}" ]] || kill "${SERVER_PID}" 2>/dev/null || true
	[[ -z "${CONTROL_PID}" ]] || kill "${CONTROL_PID}" 2>/dev/null || true
	rm -rf -- "${SANDBOX}"
}
trap cleanup EXIT

readonly RUNTIME="${SANDBOX}/run" DATA="${SANDBOX}/data" STUBS="${SANDBOX}/bin"
readonly CONTROL_SOCK="${RUNTIME}/jarvis-control/control.sock" TOKEN="tok-$$-secret-value"
readonly CONTROL_LOG="${SANDBOX}/control.log" CONTROL_MODE="${SANDBOX}/control.mode"
mkdir -p "${RUNTIME}/jarvis-control" "${DATA}" "${STUBS}"
printf '%s\n' "${TOKEN}" >"${DATA}/token"
printf '#!/usr/bin/env bash\necho "$*" >>"%s/pkill.calls"\nexit 1\n' "${SANDBOX}" >"${STUBS}/pkill"
printf '#!/usr/bin/env bash\nexit 0\n' >"${STUBS}/notify-send"
chmod +x "${STUBS}"/*
sed "s|^PATH=/usr/local/bin:/usr/bin\$|PATH=${STUBS}:/usr/local/bin:/usr/bin|" "${STOP_REAL}" >"${STOP}"
# Tools planted on the caller's PATH (e.g. by an agent in ~/.local/bin) must never run.
readonly EVIL="${SANDBOX}/evil" EVIL_LOG="${SANDBOX}/evil.calls"
mkdir -p "${EVIL}"
for t in python3 timeout pkill notify-send id; do
	printf '#!/bin/sh\necho %s >>"%s"\nexit 0\n' "${t}" "${EVIL_LOG}" >"${EVIL}/${t}"
done
chmod +x "${EVIL}"/*

PASS=0 FAIL=0
check() {
	if eval "$2"; then PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"
	else FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; printf '%s\n' "${OUT:-}" | sed 's/^/     /'; fi
}
stop() {
	set +e
	OUT="$(env -i PATH="${EVIL}:/usr/bin:/bin" HOME="${SANDBOX}/home" XDG_RUNTIME_DIR="${RUNTIME}" \
		GENTLE_DOT_DATA_DIR="${DATA}" GENTLE_DOT_PORT="${PORT}" JARVIS_STOP_NOTIFY="${NOTIFY:-0}" bash "${STOP}" 2>&1)"
	RC=$?
	set -e
}

# Fake jarvis-input control.sock: logs each request line; replies per the mode file
# (ok → {"ok":true}, error → {"ok":false,...}, silent → closes without answering).
start_control() {
	python3 -I - "${CONTROL_SOCK}" "${CONTROL_LOG}" "${CONTROL_MODE}" <<'PY' &
import os, socket, sys
path, log, mode_file = sys.argv[1:4]
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path); srv.listen(5)
while True:
    c, _ = srv.accept()
    try:
        buf = b""
        while b"\n" not in buf:
            d = c.recv(1024)
            if not d: break
            buf += d
        with open(log, "a") as f: f.write(buf.decode().strip() + "\n")
        mode = open(mode_file).read().strip() if os.path.exists(mode_file) else "ok"
        if mode == "ok": c.sendall(b'{"ok":true,"enabled":false}\n')
        elif mode == "error": c.sendall(b'{"ok":false,"error":"boom"}\n')
    finally:
        c.close()
PY
	CONTROL_PID=$!
	for _ in $(seq 50); do [[ -S "${CONTROL_SOCK}" ]] && break; sleep 0.1; done
}
stop_control() { kill "${CONTROL_PID}" 2>/dev/null || true; wait "${CONTROL_PID}" 2>/dev/null || true; CONTROL_PID=""; rm -f -- "${CONTROL_SOCK}"; }

# A free port nobody listens on.
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"

# 0. Fixed environment (S15): its own root-owned PATH before running anything, the system python
#    by absolute path, and the niri bind documented with the absolute path.
first_cmd="$(awk '!/^(#|$)/ && n < 2 {printf "%s ", $0; n++}' "${STOP_REAL}")"
OUT="${first_cmd}"
check "sets PATH=/usr/local/bin:/usr/bin and exports it before anything else" '[[ "${first_cmd}" == "PATH=/usr/local/bin:/usr/bin export PATH " ]]'
OUT="$(grep -nE '(^|[^/])python3 ' "${STOP_REAL}" | grep -v '^[0-9]*:#' || true)"
check "runs python only as /usr/bin/python3" \
	'[[ -z "${OUT}" ]] && grep -qxF "readonly PY=/usr/bin/python3" "${STOP_REAL}" && [[ "$(grep -cF "\"\${PY}\" -I -" "${STOP_REAL}")" == 2 ]]'
check "starts with #!/bin/bash, not env (env would look bash up on the caller's PATH)" '[[ "$(head -n1 "${STOP_REAL}")" == "#!/bin/bash" ]]'
check "documents the niri bind with the absolute path" 'grep -qF "spawn \"/home/<user>/.local/bin/jarvis-stop\"" "${STOP_REAL}"'
check "the test copy only adds the stub directory" '[[ "$(diff "${STOP_REAL}" "${STOP}" | grep -c "^[<>]")" == 2 ]]'

# 1. Helper and daemon down: says so, still checks wtype, exit 0.
stop
check "all down: exit 0" '[[ ${RC} -eq 0 ]]'
check "all down: says jarvis-input is not running" 'grep -qF "jarvis-input no está corriendo" <<<"${OUT}"'
check "all down: says abort was not sent" 'grep -qF "daemon no disponible" <<<"${OUT}"'
check "runs pkill -x wtype as backup" 'grep -qxF -- "-x wtype" "${SANDBOX}/pkill.calls"'

# 2. Stale socket path (nobody listening): same as down.
python3 -I -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1])' "${CONTROL_SOCK}"
stop
check "stale control.sock: exit 0 and not running" '[[ ${RC} -eq 0 ]] && grep -qF "jarvis-input no está corriendo" <<<"${OUT}"'
rm -f -- "${CONTROL_SOCK}"

# 3. Helper up: exactly one {"op":"revoke"} line, reported as revoked.
start_control
stop
check "helper up: exit 0" '[[ ${RC} -eq 0 ]]'
check "helper up: sent exactly {\"op\":\"revoke\"}" \
	'python3 -c "import json,sys; f=[json.loads(l) for l in open(sys.argv[1])]; assert f==[{\"op\":\"revoke\"}], f" "${CONTROL_LOG}"'
check "helper up: reports revoked" 'grep -qF "permiso de control revocado" <<<"${OUT}"'

# 4. Helper refuses or hangs up: reported as an error, still exit 0 and still aborts/pkills.
: >"${CONTROL_LOG}"; echo error >"${CONTROL_MODE}"
stop
check "helper error: exit 0 and reports its message" '[[ ${RC} -eq 0 ]] && grep -qF "no confirmó el revoke (boom)" <<<"${OUT}"'
echo silent >"${CONTROL_MODE}"
stop
check "helper silent: exit 0 and reports it" '[[ ${RC} -eq 0 ]] && grep -qF "ERROR: jarvis-input no confirmó el revoke" <<<"${OUT}"'
echo ok >"${CONTROL_MODE}"; : >"${CONTROL_LOG}"

# Fake daemon: accepts (or refuses with 4401 when FAKE_REFUSE exists) and logs every text frame.
readonly LOG="${SANDBOX}/frames.log" PORTFILE="${SANDBOX}/port" REFUSE="${SANDBOX}/refuse"
python3 -I - "${PORTFILE}" "${LOG}" "${REFUSE}" <<'PY' &
import base64, hashlib, os, socket, struct, sys
portfile, log, refuse = sys.argv[1:4]
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0)); srv.listen(5)
open(portfile, "w").write(str(srv.getsockname()[1]))
def rx(c, n):
    b = b""
    while len(b) < n:
        d = c.recv(n - len(b))
        if not d: raise EOFError
        b += d
    return b
while True:
    c, _ = srv.accept()
    try:
        req = b""
        while b"\r\n\r\n" not in req: req += c.recv(1024)
        key = [l.split(b":", 1)[1].strip() for l in req.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
        first = True
        while True:
            b1, b2 = rx(c, 2); n = b2 & 0x7F
            if n == 126: n = struct.unpack(">H", rx(c, 2))[0]
            mask = rx(c, 4) if b2 & 0x80 else b"\0\0\0\0"
            data = bytes(x ^ mask[i % 4] for i, x in enumerate(rx(c, n)))
            if b1 & 0x0F == 8: break
            with open(log, "a") as f: f.write(data.decode() + "\n")
            if first:
                first = False
                if os.path.exists(refuse):
                    c.sendall(bytes([0x88, 2]) + struct.pack(">H", 4401)); break
                c.sendall(bytes([0x81, 2]) + b"{}")
    except Exception:
        pass
    finally:
        c.close()
PY
SERVER_PID=$!
for _ in $(seq 50); do [[ -s "${PORTFILE}" ]] && break; sleep 0.1; done
PORT="$(<"${PORTFILE}")"

# 5. Helper and daemon up: revoke, then hello (protocol 1, token from the file) and abort; the
#    token is never printed.
NOTIFY=1 stop
check "daemon up: exit 0" '[[ ${RC} -eq 0 ]]'
check "daemon up: revoke sent and reported before the abort" \
	'grep -qxF "{\"op\":\"revoke\"}" "${CONTROL_LOG}" && grep -n "" <<<"${OUT}" | awk -F: "/revocado/{r=\$1} /abort enviado/{a=\$1} END{exit !(r && a && r<a)}"'
check "daemon up: reports abort sent" 'grep -qF "abort enviado al daemon" <<<"${OUT}"'
check "daemon got hello with protocol 1 and the token, then abort" \
	'python3 -c "import json,sys; f=[json.loads(l) for l in open(sys.argv[1])]; assert f==[{\"type\":\"hello\",\"token\":sys.argv[2],\"protocol\":1},{\"type\":\"abort\"}], f" "${LOG}" "${TOKEN}"'
check "output never contains the token" '! grep -qF "${TOKEN}" <<<"${OUT}"'

# 6. Token refused (4401): no abort sent, exit 0.
: >"${LOG}"; touch "${REFUSE}"
stop
check "refused hello: exit 0 and reports the code" '[[ ${RC} -eq 0 ]] && grep -qF "rechazó el hello (código 4401)" <<<"${OUT}"'
check "refused hello: no abort sent" '[[ "$(wc -l <"${LOG}")" -eq 1 ]] && ! grep -qF abort "${LOG}"'
rm -f "${REFUSE}"

# 7. Missing token file: no connection attempt, exit 0.
: >"${LOG}"; mv "${DATA}/token" "${DATA}/token.off"
stop
check "missing token: exit 0, abort not sent, nothing sent" '[[ ${RC} -eq 0 && ! -s "${LOG}" ]] && grep -qF "no se pudo leer el token" <<<"${OUT}"'

OUT="$(cat "${EVIL_LOG}" 2>/dev/null || true)"
check "no tool from the caller's PATH ever ran" '[[ ! -e "${EVIL_LOG}" ]]'

printf '\n%d passed, %d failed\n' "${PASS}" "${FAIL}"
((FAIL == 0))
