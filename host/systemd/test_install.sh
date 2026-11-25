#!/usr/bin/env bash
# Self-contained checks for host/systemd/install.sh and the unit template.
#
#   bash host/systemd/test_install.sh
#
# Runs without systemd and without Gentle Dot: HOME and XDG_CONFIG_HOME point into a
# temporary sandbox, and `systemctl`, `node` and `id` are stubs on PATH. Nothing is
# written outside the sandbox and the real `systemctl --user` is never called.
# `systemd-analyze --user verify` runs on the rendered unit only when it is installed.
set -euo pipefail

readonly HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly INSTALL="${HERE}/install.sh"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/gentle-dot-unit-test.XXXXXX")"
readonly SANDBOX
# KEEP_SANDBOX=1 leaves the sandbox in place for inspection.
[[ "${KEEP_SANDBOX:-0}" == 1 ]] && printf 'sandbox: %s\n' "${SANDBOX}" || trap 'rm -rf -- "${SANDBOX}"' EXIT

readonly STUBS="${SANDBOX}/bin"
readonly CALLS="${SANDBOX}/systemctl.calls"
readonly FAKE_HOME="${SANDBOX}/home"
readonly FAKE_CONFIG="${SANDBOX}/config"
readonly UNIT="${FAKE_CONFIG}/systemd/user/gentle-dot.service"
readonly GD="${SANDBOX}/gentle-dot"
# Agent rules: shipped in host/gentle-dot/, installed under the default agent home.
readonly RULES_SRC="$(cd -- "${HERE}/.." && pwd)/gentle-dot/AGENTS.md"
readonly RULES="${FAKE_HOME}/.gentle-dot/agent/AGENTS.md"
readonly RULES_BAK="${RULES}.bak"
# PC control (S12): extension and emergency stop.
readonly EXT_SRC="$(cd -- "${HERE}/.." && pwd)/gentle-dot/extensions"
readonly EXT="${FAKE_HOME}/.gentle-dot/agent/extensions/jarvis-pc-control"
readonly EXT_BAK="${FAKE_HOME}/.gentle-dot/agent/jarvis-pc-control.bak"
readonly STOP_SRC="$(cd -- "${HERE}/.." && pwd)/bin/jarvis-stop"
readonly STOP="${FAKE_HOME}/.local/bin/jarvis-stop"
# jarvis-input helper (S13): a fake package stands in for host/input/jarvis_input/ (built by
# another task); JARVIS_INPUT_SRC_DIR points install.sh at it.
readonly INPUT_SRC="${SANDBOX}/input-src/jarvis_input"
readonly INPUT_ROOT="${FAKE_HOME}/.local/share/jarvis/input"
readonly INPUT="${INPUT_ROOT}/jarvis_input"
readonly INPUT_BAK="${INPUT_ROOT}/jarvis_input.bak"
readonly INPUT_UNIT="${FAKE_CONFIG}/systemd/user/jarvis-input.service"
readonly SBX_SRC="${HERE}/jarvis-sandbox-exec"
readonly SBX="${FAKE_HOME}/.local/share/jarvis/sandbox/jarvis-sandbox-exec"
readonly APPS="${FAKE_HOME}/.local/share/applications"
# S15: the daemon's two writable folders (its data and the user's work folder).
readonly WORK="${FAKE_HOME}/Jarvis"
readonly GD_DATA="${FAKE_HOME}/.gentle-dot"
# S15: the TUI, installed like the helper. A fake package and requirements stand in for
# host/jarvis/ and host/requirements.txt; a stub base python (JARVIS_TUI_PYTHON) makes the
# venv, whose stub python logs pip calls instead of downloading anything.
readonly TUI_SRC="${SANDBOX}/tui-src/jarvis"
readonly TUI_REQ_SRC="${SANDBOX}/tui-src/requirements.txt"
readonly TUI_ROOT="${FAKE_HOME}/.local/share/jarvis/tui"
readonly TUI="${TUI_ROOT}/jarvis"
readonly TUI_BAK="${TUI_ROOT}/jarvis.bak"
readonly TUI_VENV="${TUI_ROOT}/.venv"
readonly TUI_LAUNCHER="${FAKE_HOME}/.local/bin/jarvis"
readonly PY_CALLS="${SANDBOX}/python.calls"
readonly FAIL_PIP="${SANDBOX}/fail-pip"
readonly TUI_HINT="La TUI cambió: ciérrala y vuelve a abrirla con ${FAKE_HOME}/.local/bin/jarvis."
mkdir -p "${TUI_SRC}/__pycache__"
printf '"""fake tui"""\n' >"${TUI_SRC}/__init__.py"
printf 'import sys\nprint("tui", __name__, sys.argv[1:], sys.flags.isolated)\n' >"${TUI_SRC}/tui.py"
printf 'junk' >"${TUI_SRC}/__pycache__/tui.cpython-314.pyc"
printf 'websockets>=13.0\ntextual>=0.79\n' >"${TUI_REQ_SRC}"
readonly INPUT_HINT="El ayudante jarvis-input cambió: si ya corre, reinícialo para aplicarlo: systemctl --user try-restart jarvis-input.service (o este script con --enable)."
readonly SANDBOX_HINT="El aislamiento de gentle-dot.service se aplica al arrancar el daemon: si ya corre, reinícialo: systemctl --user try-restart gentle-dot.service (o este script con --enable)."
mkdir -p "${INPUT_SRC}/sub" "${INPUT_SRC}/__pycache__"
printf '"""fake helper"""\n' >"${INPUT_SRC}/__init__.py"
printf 'print("fake")\n' >"${INPUT_SRC}/__main__.py"
printf 'X = 1\n' >"${INPUT_SRC}/sub/x.py"
printf 'junk' >"${INPUT_SRC}/__pycache__/junk.cpython-314.pyc"
readonly EXT_HINT="La extensión de control de la PC se carga al arrancar el motor: si el servicio ya corre, reinícialo para aplicarla: systemctl --user try-restart gentle-dot.service (o este script con --enable)."
readonly RESTART_HINT="Las reglas del agente se leen al arrancar el motor: si el servicio ya corre, reinícialo para aplicarlas: systemctl --user try-restart gentle-dot.service (o este script con --enable)."
mkdir -p "${STUBS}" "${FAKE_HOME}" "${GD}/packages/daemon/src" "${GD}/node_modules"
: >"${GD}/packages/daemon/src/cli.ts"
: >"${CALLS}"

# systemctl stub: records its arguments, never touches a manager. While the file
# ${FAIL_RELOAD} exists, the next `daemon-reload` fails once and removes it.
readonly FAIL_RELOAD="${SANDBOX}/fail-reload"
cat >"${STUBS}/systemctl" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"${CALLS}"
if [[ "\$*" == "--user daemon-reload" && -e "${FAIL_RELOAD}" ]]; then
	rm -f -- "${FAIL_RELOAD}"
	echo "Failed to reload daemon: stub" >&2
	exit 1
fi
EOF
# Base python stub for the TUI venv: `-m venv DIR` makes DIR/bin/python, itself a stub that
# logs its arguments and fails `-m pip` while ${FAIL_PIP} exists.
cat >"${STUBS}/python3-tui" <<EOF
#!/usr/bin/env bash
printf 'base %s\n' "\$*" >>"${PY_CALLS}"
[[ "\$1 \$2" == "-m venv" ]] || exit 2
d="\${@: -1}"
mkdir -p "\${d}/bin"
cat >"\${d}/bin/python" <<'PY'
#!/usr/bin/env bash
printf 'venv %s\n' "\$*" >>"${PY_CALLS}"
[[ ! -e "${FAIL_PIP}" ]]
PY
chmod +x "\${d}/bin/python"
EOF
# node stubs: a current one and one that is too old.
printf '#!/usr/bin/env bash\necho v24.11.0\n' >"${STUBS}/node"
printf '#!/usr/bin/env bash\necho v20.19.0\n' >"${STUBS}/node20"
# id stub that claims root, used only by the root-refusal case.
mkdir -p "${SANDBOX}/rootbin"
printf '#!/usr/bin/env bash\necho 0\n' >"${SANDBOX}/rootbin/id"
chmod +x "${STUBS}"/* "${SANDBOX}/rootbin/id"

PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; [[ -z "${2:-}" ]] || printf '%s\n' "$2" | sed 's/^/     /'; }
check() { if eval "$2"; then ok "$1"; else ko "$1" "${OUT:-}"; fi; }

# sandboxed ARGS...: run install.sh with the sandbox environment; sets OUT and RC.
sandboxed() {
	set +e
	OUT="$(env -i PATH="${STUBS}:/usr/bin:/bin" HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${CFG:-${FAKE_CONFIG}}" \
		JARVIS_INPUT_SRC_DIR="${INPUT_SRC_OVERRIDE:-${INPUT_SRC}}" JARVIS_TUI_SRC_DIR="${TUI_SRC_OVERRIDE:-${TUI_SRC}}" \
		JARVIS_TUI_REQUIREMENTS="${TUI_REQ_SRC}" JARVIS_TUI_PYTHON="${STUBS}/python3-tui" bash "${INSTALL}" "$@" 2>&1)"
	RC=$?
	set -e
}
calls() { cat "${CALLS}"; }
reset_calls() { : >"${CALLS}"; }
readonly BASE=(--gentle-dot-dir "${GD}" --node "${STUBS}/node" --allow-unpinned)

# 1. Dry-run install: prints the unit and the reload, writes and calls nothing.
sandboxed "${BASE[@]}" --dry-run
check "dry-run install exits 0" '[[ ${RC} -eq 0 ]]'
check "dry-run install shows ExecStart" \
	'grep -qF "ExecStart=/usr/bin/python3 -I \"${SBX}\" \"${STUBS}/node\" \"${GD}/packages/daemon/src/cli.ts\"" <<<"${OUT}"'
check "dry-run install shows daemon-reload" 'grep -qF "[dry-run] systemctl --user daemon-reload" <<<"${OUT}"'
check "dry-run install writes nothing" '[[ ! -e "${FAKE_CONFIG}" ]]'
check "dry-run install calls no systemctl" '[[ ! -s "${CALLS}" ]]'
check "dry-run install announces the agent rules" 'grep -qxF "[dry-run] escribir ${RULES} (desde ${RULES_SRC})" <<<"${OUT}"'
check "dry-run install announces the extension and the stop" \
	'grep -qxF "[dry-run] escribir ${EXT} (desde ${EXT_SRC})" <<<"${OUT}" && grep -qxF "[dry-run] escribir ${STOP} (desde ${STOP_SRC})" <<<"${OUT}"'
check "dry-run install writes nothing under HOME" '[[ -z "$(ls -A "${FAKE_HOME}")" ]]'
check "dry-run install announces the TUI, its venv, the launcher and the work folder" \
	'grep -qxF "[dry-run] escribir ${TUI} (desde ${TUI_SRC})" <<<"${OUT}" &&
	 grep -qxF "[dry-run] ${STUBS}/python3-tui -m venv ${TUI_VENV} && pip install -r ${TUI_REQ_SRC}" <<<"${OUT}" &&
	 grep -qxF "[dry-run] escribir ${TUI_LAUNCHER}" <<<"${OUT}" && grep -qxF "[dry-run] mkdir -m 0700 ${WORK}" <<<"${OUT}" &&
	 [[ ! -e "${PY_CALLS}" ]]'
check "dry-run install announces the helper, its unit and the sandbox wrapper" \
	'grep -qxF "[dry-run] escribir ${INPUT} (desde ${INPUT_SRC})" <<<"${OUT}" &&
	 grep -qxF "[dry-run] escribir ${INPUT_UNIT}:" <<<"${OUT}" &&
	 grep -qxF "[dry-run] escribir ${SBX} (desde ${SBX_SRC})" <<<"${OUT}"'

# 2. --check: verifies only.
sandboxed "${BASE[@]}" --check
check "--check exits 0 and writes nothing" '[[ ${RC} -eq 0 && ! -e "${FAKE_CONFIG}" && ! -s "${CALLS}" && -z "$(ls -A "${FAKE_HOME}")" ]]'
check "--check reports missing agent rules" 'grep -qxF "Reglas del agente: faltan en ${RULES} (install.sh las instala)" <<<"${OUT}"'
check "--check reports the missing extension and stop" \
	'grep -qxF "Extensión de control de la PC: falta en ${EXT} (install.sh la instala)" <<<"${OUT}" && grep -qxF "Parada: falta en ${STOP} (install.sh la instala)" <<<"${OUT}"'
check "--check reports the missing TUI, its venv and launcher, and the work folder" \
	'grep -qxF "TUI: falta en ${TUI} (install.sh la instala)" <<<"${OUT}" && grep -qxF "Entorno de la TUI: falta en ${TUI_VENV} (install.sh lo crea con pip)" <<<"${OUT}" &&
	 grep -qxF "Lanzador de la TUI: falta en ${TUI_LAUNCHER} (install.sh lo instala)" <<<"${OUT}" &&
	 grep -qxF "Carpeta de trabajo: falta ${WORK} (install.sh la crea, 0700)" <<<"${OUT}" && [[ ! -e "${PY_CALLS}" ]]'
check "--check reports the missing helper and sandbox wrapper" \
	'grep -qxF "Ayudante jarvis-input: falta en ${INPUT} (install.sh lo instala)" <<<"${OUT}" && grep -qxF "Envoltorio de aislamiento: falta en ${SBX} (install.sh lo instala)" <<<"${OUT}"'

# 3. Install: writes the rendered unit and reloads; does not enable.
sandboxed "${BASE[@]}"
check "install exits 0" '[[ ${RC} -eq 0 ]]'
check "install writes the unit" '[[ -f "${UNIT}" ]]'
check "unit has no placeholders left" '! grep -q "@[A-Z_]*@" "${UNIT}"'
check "unit runs the daemon through the Landlock wrapper" \
	'grep -qxF "ExecStart=/usr/bin/python3 -I \"${SBX}\" \"${STUBS}/node\" \"${GD}/packages/daemon/src/cli.ts\"" "${UNIT}" &&
	 grep -qxF "ExecStartPre=/usr/bin/python3 -I \"${SBX}\" \"${STUBS}/node\" \"${GD}/packages/daemon/src/cli.ts\" --self-check" "${UNIT}"'
check "install only reloads" '[[ "$(calls)" == "--user daemon-reload" ]]'
check "unit is 0644" '[[ "$(stat -c %a "${UNIT}")" == 644 ]]'
check "nothing outside the user unit dir, the agent rules, the extension, the stop, the helper, the wrapper, the TUI and the work folder" \
	'[[ "$(cd "${FAKE_CONFIG}" && find . -mindepth 1 | LC_ALL=C sort | tr "\n" " ")" == "./systemd ./systemd/user ./systemd/user/gentle-dot.service ./systemd/user/jarvis-input.service " &&
	   "$(cd "${FAKE_HOME}" && find . -mindepth 1 | LC_ALL=C sort | tr "\n" " ")" == "./.gentle-dot ./.gentle-dot/agent ./.gentle-dot/agent/AGENTS.md ./.gentle-dot/agent/extensions ./.gentle-dot/agent/extensions/jarvis-pc-control ./.gentle-dot/agent/extensions/jarvis-pc-control/index.ts ./.gentle-dot/agent/extensions/jarvis-pc-control/pc-control-core.ts ./.local ./.local/bin ./.local/bin/jarvis ./.local/bin/jarvis-stop ./.local/share ./.local/share/applications ./.local/share/jarvis ./.local/share/jarvis/input ./.local/share/jarvis/input/jarvis_input ./.local/share/jarvis/input/jarvis_input/__init__.py ./.local/share/jarvis/input/jarvis_input/__main__.py ./.local/share/jarvis/input/jarvis_input/sub ./.local/share/jarvis/input/jarvis_input/sub/x.py ./.local/share/jarvis/sandbox ./.local/share/jarvis/sandbox/jarvis-sandbox-exec ./.local/share/jarvis/tui ./.local/share/jarvis/tui/.venv ./.local/share/jarvis/tui/.venv/bin ./.local/share/jarvis/tui/.venv/bin/python ./.local/share/jarvis/tui/.venv/jarvis-requirements.txt ./.local/share/jarvis/tui/jarvis ./.local/share/jarvis/tui/jarvis/__init__.py ./.local/share/jarvis/tui/jarvis/tui.py ./.local/share/jarvis/tui/requirements.txt ./Jarvis " ]]'
check "install copies the helper package without __pycache__, files 0644" \
	'cmp -s "${INPUT_SRC}/__init__.py" "${INPUT}/__init__.py" && cmp -s "${INPUT_SRC}/__main__.py" "${INPUT}/__main__.py" &&
	 cmp -s "${INPUT_SRC}/sub/x.py" "${INPUT}/sub/x.py" && [[ ! -e "${INPUT}/__pycache__" && "$(stat -c %a "${INPUT}/__main__.py")" == 644 ]] &&
	 grep -qxF "Escrito ${INPUT}" <<<"${OUT}"'
check "install writes the sandbox wrapper verbatim, 0644" 'cmp -s "${SBX_SRC}" "${SBX}" && [[ "$(stat -c %a "${SBX}")" == 644 ]]'
check "install writes jarvis-input.service 0644 and prints the helper hint" \
	'[[ -f "${INPUT_UNIT}" && "$(stat -c %a "${INPUT_UNIT}")" == 644 ]] && cmp -s "${HERE}/jarvis-input.service.in" "${INPUT_UNIT}" && grep -qxF "${INPUT_HINT}" <<<"${OUT}"'
check "a changed daemon unit without --enable prints the sandbox restart hint" 'grep -qxF "${SANDBOX_HINT}" <<<"${OUT}"'

# 3b. Daemon sandbox (S13): every read-only path exists before the daemon can start (no "-"
# prefix: a missing path would be skipped by systemd and then be writable, even creatable, by
# the agent; without the prefix the daemon refuses to start instead).
svc_section="$(awk '/^\[/{s=$0; next} s=="[Service]"' "${UNIT}")"
# S15: the home is read-only (it covers ~/.local/share/jarvis, applications, ~/.config, niri,
# mimeapps.list, ~/.local/bin); the unit still names the paths that may live outside the home
# (XDG_CONFIG_HOME, --bin-dir) and the agent's own files inside its writable ~/.gentle-dot.
ro_line="ReadOnlyPaths=${FAKE_CONFIG}/systemd/user ${STOP} ${TUI_LAUNCHER} ${FAKE_HOME}/.gentle-dot/agent/extensions ${RULES}"
check "daemon unit has the exact ReadOnlyPaths, no optional prefix" 'grep -qxF "${ro_line}" <<<"${svc_section}"'
check "every read-only and read-write path exists after install" \
	'(for p in "${FAKE_CONFIG}/systemd/user" "${STOP}" "${TUI_LAUNCHER}" "${FAKE_HOME}/.gentle-dot/agent/extensions" "${RULES}" "${GD_DATA}" "${WORK}"; do [[ -e "$p" ]] || exit 1; done)'
check "install creates the work folder ~/Jarvis 0700 and says so" \
	'[[ -d "${WORK}" && "$(stat -c %a "${WORK}")" == 700 ]] && grep -qxF "Carpeta de trabajo del agente: ${WORK}" <<<"${OUT}"'
check "daemon unit has no ReadWritePaths with an optional prefix" '! grep -qE "^ReadWritePaths=.*(^| )-" <<<"${svc_section}"'
for directive in "PrivateUsers=yes" "PrivateDevices=yes" "TemporaryFileSystem=%t:mode=0700" \
	"PrivateTmp=disconnected" "TemporaryFileSystem=/dev/shm" \
	"RuntimeDirectory=jarvis" "RuntimeDirectoryMode=0700" "RuntimeDirectoryPreserve=yes" "BindReadOnlyPaths=%t/jarvis" \
	"ProtectHome=read-only" "ReadWritePaths=%h/.gentle-dot %h/Jarvis" \
	"InaccessiblePaths=-%t/bus -%t/jarvis-control -%t/systemd -/tmp/.X11-unix -/run/docker.sock -/run/dbus/system_bus_socket" \
	"UnsetEnvironment=WAYLAND_DISPLAY DISPLAY NIRI_SOCKET DBUS_SESSION_BUS_ADDRESS" "NoNewPrivileges=yes"; do
	check "daemon unit sets ${directive}" 'grep -qxF "${directive}" <<<"${svc_section}"'
done
check "every sandbox directive in the daemon unit has a comment right above it" \
	'awk '"'"'/^\[Service\]/{s=1} /^\[Install\]/{s=0} s && /^(PrivateUsers|PrivateDevices|PrivateTmp|TemporaryFileSystem|RuntimeDirectory|BindReadOnlyPaths|InaccessiblePaths|ReadOnlyPaths|UnsetEnvironment|ProtectHome|ReadWritePaths)=/ && prev !~ /^#/ && prevkey !~ /^RuntimeDirectory/ {bad=1; print} {prev=$0; prevkey=$0} END{exit bad}'"'"' "${UNIT}"'
check "daemon unit keeps the graphical-session binding" 'grep -qxF "WantedBy=graphical-session.target" "${UNIT}"'

# 3c. jarvis-input.service: the only unit with the display; no runtime-dir sandbox.
input_unit_section="$(awk '/^\[/{s=$0; next} s=="[Unit]"' "${INPUT_UNIT}")"
input_svc_section="$(awk '/^\[/{s=$0; next} s=="[Service]"' "${INPUT_UNIT}")"
for directive in "After=graphical-session.target" "PartOf=graphical-session.target" "StartLimitIntervalSec=10min" "StartLimitBurst=10"; do
	check "helper unit sets ${directive} in [Unit]" 'grep -qxF "${directive}" <<<"${input_unit_section}"'
done
# S15: isolated interpreter (-I: no PYTHON* variables, no user site, no cwd or script dir on
# sys.path), the package dir named explicitly; no loader or interpreter injection variables.
helper_exec="ExecStart=/usr/bin/python3 -I -B -c \"import runpy, sys; sys.path.insert(0, '%h/.local/share/jarvis/input'); runpy.run_module('jarvis_input', run_name='__main__', alter_sys=True)\""
for directive in "${helper_exec}" "WorkingDirectory=%h/.local/share/jarvis/input" \
	"Environment=PATH=/usr/local/bin:/usr/bin" \
	"UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE PYTHONINSPECT NODE_OPTIONS" \
	"RuntimeDirectory=jarvis jarvis-control" "RuntimeDirectoryMode=0700" "RuntimeDirectoryPreserve=yes" \
	"Restart=on-failure" "NoNewPrivileges=yes" "UMask=0077"; do
	check "helper unit sets ${directive}" 'grep -qxF "${directive}" <<<"${input_svc_section}"'
done
check "helper unit has a single ExecStart and no PYTHONPATH of its own" \
	'[[ "$(grep -c "^ExecStart=" "${INPUT_UNIT}")" == 1 ]] && ! grep -q "^Environment=PYTHONPATH" "${INPUT_UNIT}"'
check "helper unit does not hide the runtime dir or the display" \
	'! grep -qE "^(PrivateTmp|PrivateUsers|TemporaryFileSystem|InaccessiblePaths|PrivateDevices|ProtectHome)=" "${INPUT_UNIT}" &&
	 ! grep -E "^UnsetEnvironment=" "${INPUT_UNIT}" | grep -qE "WAYLAND_DISPLAY|NIRI_SOCKET|DISPLAY"'
check "helper unit is WantedBy=graphical-session.target" \
	'[[ "$(awk '"'"'/^\[/{s=$0; next} s=="[Install]"'"'"' "${INPUT_UNIT}" | grep -E "^WantedBy=")" == "WantedBy=graphical-session.target" ]]'
check "install writes the extension: entry as index.ts, core beside it, 0644" \
	'cmp -s "${EXT_SRC}/pc-control.ts" "${EXT}/index.ts" && cmp -s "${EXT_SRC}/pc-control-core.ts" "${EXT}/pc-control-core.ts" &&
	 [[ "$(stat -c %a "${EXT}/index.ts")" == 644 && "$(stat -c %a "${EXT}/pc-control-core.ts")" == 644 ]] && grep -qxF "Escrito ${EXT}" <<<"${OUT}"'
check "the agent home stays private" '[[ "$(stat -c %a "${FAKE_HOME}/.gentle-dot")" == 700 && "$(stat -c %a "${FAKE_HOME}/.gentle-dot/agent/extensions")" == 700 ]]'
check "install writes jarvis-stop verbatim, 0755" 'cmp -s "${STOP_SRC}" "${STOP}" && [[ "$(stat -c %a "${STOP}")" == 755 ]] && grep -qxF "Escrito ${STOP}" <<<"${OUT}"'
check "new extension without --enable prints its restart hint" 'grep -qxF "${EXT_HINT}" <<<"${OUT}"'
check "install prints the niri stop binding with the absolute path, warns when the bin dir is off PATH" \
	'grep -qF "Alt+Shift+Escape { spawn \"${STOP}\"; }" <<<"${OUT}" && ! grep -qF "spawn \"jarvis-stop\"" <<<"${OUT}" &&
	 grep -qF "aviso: ${FAKE_HOME}/.local/bin no está en PATH" <<<"${OUT}"'
check "install writes the agent rules verbatim" '[[ -f "${RULES}" ]] && cmp -s "${RULES_SRC}" "${RULES}" && grep -qxF "Escrito ${RULES}" <<<"${OUT}"'
check "agent rules are 0644" '[[ "$(stat -c %a "${RULES}")" == 644 ]]'
check "new agent rules without --enable print the restart hint" 'grep -qxF "${RESTART_HINT}" <<<"${OUT}"'

# 3d. TUI (S15): a copy of the package and requirements in ~/.local/share/jarvis/tui with its own
# venv (base python + pip -r), and ~/.local/bin/jarvis as a launcher with absolute paths.
launch_exec="exec \"${TUI_VENV}/bin/python\" -I -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module(\"jarvis.tui\", run_name=\"__main__\", alter_sys=True)' \"${TUI_ROOT}\" \"\$@\""
check "install copies the TUI package without __pycache__, files 0644, and its requirements" \
	'cmp -s "${TUI_SRC}/__init__.py" "${TUI}/__init__.py" && cmp -s "${TUI_SRC}/tui.py" "${TUI}/tui.py" &&
	 [[ ! -e "${TUI}/__pycache__" && "$(stat -c %a "${TUI}/tui.py")" == 644 ]] && cmp -s "${TUI_REQ_SRC}" "${TUI_ROOT}/requirements.txt" &&
	 grep -qxF "Escrito ${TUI}" <<<"${OUT}"'
check "install builds the venv with the base python, then pip -r, isolated" \
	'grep -qE "^base -m venv ${TUI_ROOT}/\.venv\.new\.[0-9]+$" "${PY_CALLS}" &&
	 grep -qxF "venv -I -m pip install --disable-pip-version-check --no-input --timeout 20 --retries 1 -q -r ${TUI_REQ_SRC}" "${PY_CALLS}" &&
	 [[ -x "${TUI_VENV}/bin/python" ]] && cmp -s "${TUI_REQ_SRC}" "${TUI_VENV}/jarvis-requirements.txt" &&
	 grep -qxF "Entorno de la TUI listo: ${TUI_VENV}" <<<"${OUT}" && [[ -z "$(find "${TUI_ROOT}" -maxdepth 1 -name ".venv.new.*")" ]]'
check "install writes the launcher 0755 with absolute paths" \
	'[[ "$(stat -c %a "${TUI_LAUNCHER}")" == 755 && "$(head -n1 "${TUI_LAUNCHER}")" == "#!/bin/sh" ]] &&
	 grep -qxF -- "${launch_exec}" "${TUI_LAUNCHER}" && grep -qxF "Escrito ${TUI_LAUNCHER}" <<<"${OUT}"'
sed "s|\"${TUI_VENV}/bin/python\"|/usr/bin/python3|" "${TUI_LAUNCHER}" >"${SANDBOX}/launcher-real-python"
(cd "${SANDBOX}" && OUT="$(env -i PATH=/usr/bin:/bin PYTHONPATH=/nonexistent sh "${SANDBOX}/launcher-real-python" --theme paper 2>&1)"; printf '%s' "${OUT}" >"${SANDBOX}/launcher.out")
OUT="$(cat "${SANDBOX}/launcher.out")"
launch_expected="tui __main__ ['--theme', 'paper'] 1"
check "the launcher runs jarvis.tui as __main__, isolated, with the arguments, from any cwd" \
	'[[ "${OUT}" == "${launch_expected}" ]]'

# 4. Idempotency: a second install changes nothing and calls nothing.
before="$(sha256sum "${UNIT}")"
reset_calls
sandboxed "${BASE[@]}"
check "second install exits 0" '[[ ${RC} -eq 0 ]]'
check "second install reports no changes" 'grep -q "Sin cambios" <<<"${OUT}"'
check "second install leaves the unit" '[[ "$(sha256sum "${UNIT}")" == "${before}" ]]'
check "second install calls no systemctl" '[[ ! -s "${CALLS}" ]]'
check "an unchanged install keeps no backup" '[[ ! -e "${UNIT}.bak" ]]'
check "second install reports the agent rules unchanged, no hint, no backup" \
	'grep -qxF "Sin cambios: ${RULES} ya está al día." <<<"${OUT}" && ! grep -qxF "${RESTART_HINT}" <<<"${OUT}" && [[ ! -e "${RULES_BAK}" ]]'
check "second install reports the extension and stop unchanged, no hint, no backup" \
	'grep -qxF "Sin cambios: ${EXT} ya está al día." <<<"${OUT}" && grep -qxF "Sin cambios: ${STOP} ya está al día." <<<"${OUT}" &&
	 ! grep -qxF "${EXT_HINT}" <<<"${OUT}" && [[ ! -e "${EXT_BAK}" && ! -e "${STOP}.bak" ]]'
check "second install reports the helper, its unit and the wrapper unchanged, no hints" \
	'grep -qxF "Sin cambios: ${INPUT} ya está al día." <<<"${OUT}" && grep -qxF "Sin cambios: ${INPUT_UNIT} ya está al día." <<<"${OUT}" &&
	 grep -qxF "Sin cambios: ${SBX} ya está al día." <<<"${OUT}" && ! grep -qxF "${INPUT_HINT}" <<<"${OUT}" &&
	 ! grep -qxF "${SANDBOX_HINT}" <<<"${OUT}" && [[ ! -e "${INPUT_BAK}" ]]'
mkdir -p "${INPUT}/__pycache__" && printf 'x' >"${INPUT}/__pycache__/__main__.cpython-314.pyc"
reset_calls
sandboxed "${BASE[@]}"
check "bytecode caches in the installed helper do not count as a change" \
	'[[ ${RC} -eq 0 && ! -s "${CALLS}" ]] && grep -qxF "Sin cambios: ${INPUT} ya está al día." <<<"${OUT}"'
check "second install leaves the TUI, venv and launcher alone, runs no python" \
	'grep -qxF "Sin cambios: ${TUI} ya está al día." <<<"${OUT}" && grep -qxF "Sin cambios: ${TUI_VENV} ya está al día." <<<"${OUT}" &&
	 grep -qxF "Sin cambios: ${TUI_LAUNCHER} ya está al día." <<<"${OUT}" && ! grep -qxF "${TUI_HINT}" <<<"${OUT}" &&
	 [[ "$(wc -l <"${PY_CALLS}")" == 2 && ! -e "${TUI_BAK}" && ! -e "${TUI_LAUNCHER}.bak" && ! -e "${TUI_VENV}.bak" ]]'
sandboxed "${BASE[@]}" --check
check "--check reports the current TUI, venv, launcher and work folder" \
	'grep -qxF "TUI al día: ${TUI}" <<<"${OUT}" && grep -qxF "Entorno de la TUI al día: ${TUI_VENV}" <<<"${OUT}" &&
	 grep -qxF "Lanzador de la TUI al día: ${TUI_LAUNCHER}" <<<"${OUT}" && grep -qxF "Carpeta de trabajo: ${WORK}" <<<"${OUT}"'

# 4b. TUI changes. An edited launcher (like the old bash one that ran the main checkout) and an
# edited package are backed up and replaced; the TUI hint is printed and no unit restarts.
printf '#!/usr/bin/env bash\nexec old-checkout\n' >"${TUI_LAUNCHER}"
printf '# edit\n' >>"${TUI}/tui.py"
launcher_old="$(sha256sum <"${TUI_LAUNCHER}")"
reset_calls
sandboxed "${BASE[@]}" --check
check "--check reports the outdated TUI and launcher, changes nothing" \
	'[[ ${RC} -eq 0 && "$(sha256sum <"${TUI_LAUNCHER}")" == "${launcher_old}" && ! -e "${TUI_BAK}" ]] &&
	 grep -qxF "TUI desactualizada: ${TUI} (install.sh la reemplaza y guarda la anterior en ${TUI_BAK})" <<<"${OUT}" &&
	 grep -qxF "Lanzador de la TUI desactualizado: ${TUI_LAUNCHER} (install.sh lo reemplaza y guarda el anterior en ${TUI_LAUNCHER}.bak)" <<<"${OUT}"'
sandboxed "${BASE[@]}" --dry-run
check "dry-run on a changed TUI and launcher announces the backups only" \
	'grep -qxF "[dry-run] copia de la TUI anterior en ${TUI_BAK}" <<<"${OUT}" && grep -qxF "[dry-run] copia del lanzador anterior en ${TUI_LAUNCHER}.bak" <<<"${OUT}" &&
	 [[ "$(sha256sum <"${TUI_LAUNCHER}")" == "${launcher_old}" && ! -e "${TUI_BAK}" && ! -e "${TUI_LAUNCHER}.bak" ]]'
sandboxed "${BASE[@]}" --enable
check "changed TUI and launcher: backups, exact copies, hint, only enable --now" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user enable --now jarvis-input.service\n--user enable --now gentle-dot.service'"'"' ]] &&
	 [[ "$(sha256sum <"${TUI_LAUNCHER}.bak")" == "${launcher_old}" && "$(stat -c %a "${TUI_LAUNCHER}.bak")" == 644 ]] &&
	 grep -qxF -- "${launch_exec}" "${TUI_LAUNCHER}" && grep -qxF "# edit" "${TUI_BAK}/tui.py" && cmp -s "${TUI_SRC}/tui.py" "${TUI}/tui.py" &&
	 grep -qxF "Copia del lanzador anterior: ${TUI_LAUNCHER}.bak" <<<"${OUT}" && grep -qxF "Copia de la TUI anterior: ${TUI_BAK}" <<<"${OUT}" &&
	 grep -qxF "${TUI_HINT}" <<<"${OUT}" && [[ "$(wc -l <"${PY_CALLS}")" == 2 ]]'

# 4c. Requirements changed while offline: pip fails, the previous venv stays, the run goes on
# and the next run tries again; once pip works, the new venv replaces it (old one in .venv.bak).
printf 'marker\n' >"${TUI_VENV}/previous"
printf 'websockets>=14.0\ntextual>=0.79\n' >"${TUI_REQ_SRC}"
sandboxed "${BASE[@]}" --check
check "--check reports an outdated venv" 'grep -qxF "Entorno de la TUI desactualizado: ${TUI_VENV} (install.sh lo vuelve a crear con pip)" <<<"${OUT}"'
touch "${FAIL_PIP}"
: >"${PY_CALLS}"
reset_calls
sandboxed "${BASE[@]}" --enable
check "pip failure keeps the previous venv, warns, exits 0" \
	'[[ ${RC} -eq 0 && -f "${TUI_VENV}/previous" ]] && ! cmp -s "${TUI_REQ_SRC}" "${TUI_VENV}/jarvis-requirements.txt" &&
	 grep -qxF "aviso: pip install falló (¿sin red?); se conserva el entorno anterior de la TUI en ${TUI_VENV}. Vuelve a ejecutar este script para reintentar." <<<"${OUT}" &&
	 [[ -z "$(find "${TUI_ROOT}" -maxdepth 1 -name ".venv.new.*")" && ! -e "${TUI_VENV}.bak" ]] &&
	 [[ "$(calls)" == $'"'"'--user enable --now jarvis-input.service\n--user enable --now gentle-dot.service'"'"' ]]'
rm -f -- "${FAIL_PIP}"
: >"${PY_CALLS}"
sandboxed "${BASE[@]}"
check "the next run retries and replaces the venv, keeping the old one in .venv.bak" \
	'[[ ${RC} -eq 0 && "$(wc -l <"${PY_CALLS}")" == 2 && -f "${TUI_VENV}.bak/previous" && ! -e "${TUI_VENV}/previous" ]] &&
	 cmp -s "${TUI_REQ_SRC}" "${TUI_VENV}/jarvis-requirements.txt" && cmp -s "${TUI_REQ_SRC}" "${TUI_ROOT}/requirements.txt" &&
	 grep -qxF "Copia del entorno anterior: ${TUI_VENV}.bak" <<<"${OUT}"'
mv -- "${TUI_VENV}" "${SANDBOX}/venv-aside"
touch "${FAIL_PIP}"
sandboxed "${BASE[@]}"
check "pip failure with no venv at all warns that the TUI cannot start yet, still writes the launcher" \
	'[[ ${RC} -eq 0 && ! -e "${TUI_VENV}" && -x "${TUI_LAUNCHER}" ]] &&
	 grep -qxF "aviso: pip install falló (¿sin red?); la TUI no arrancará hasta que exista ${TUI_VENV}. Vuelve a ejecutar este script con red." <<<"${OUT}"'
rm -f -- "${FAIL_PIP}"
rm -rf -- "${TUI_VENV}.bak"
mv -- "${SANDBOX}/venv-aside" "${TUI_VENV}"
TUI_SRC_OVERRIDE="${SANDBOX}/no-such-tui" sandboxed "${BASE[@]}" --check
check "refuses when the TUI package has no tui.py" '[[ ${RC} -ne 0 ]] && grep -qF "falta la TUI ${SANDBOX}/no-such-tui" <<<"${OUT}"'
chmod 0755 "${WORK}" && : >"${WORK}/note.txt"
sandboxed "${BASE[@]}"
check "an existing work folder is left as it is" '[[ ${RC} -eq 0 && "$(stat -c %a "${WORK}")" == 755 && -f "${WORK}/note.txt" ]]'
chmod 0700 "${WORK}" && rm -f -- "${WORK}/note.txt"
sandboxed "${BASE[@]}" --check
check "--check reports current agent rules" '[[ ${RC} -eq 0 ]] && grep -qxF "Reglas del agente al día: ${RULES}" <<<"${OUT}"'
check "--check reports the current extension and stop" \
	'grep -qxF "Extensión de control de la PC al día: ${EXT}" <<<"${OUT}" && grep -qxF "Parada al día: ${STOP}" <<<"${OUT}"'
check "--check reports the current helper and wrapper" \
	'grep -qxF "Ayudante jarvis-input al día: ${INPUT}" <<<"${OUT}" && grep -qxF "Envoltorio de aislamiento al día: ${SBX}" <<<"${OUT}"'

# 5. --enable on unchanged units: only enable --now, the helper first.
reset_calls
sandboxed "${BASE[@]}" --enable
check "--enable exits 0" '[[ ${RC} -eq 0 ]]'
check "--enable runs enable --now only, helper first" \
	'[[ "$(calls)" == $'"'"'--user enable --now jarvis-input.service\n--user enable --now gentle-dot.service'"'"' ]]'

# 5j. Helper code changed (or a file planted in it): backed up, replaced exactly, the helper
# restarted; the daemon is not.
printf '# my edit\n' >>"${INPUT}/__main__.py"
printf 'evil\n' >"${INPUT}/planted.py"
helper_edited="$(sha256sum <"${INPUT}/__main__.py")"
reset_calls
sandboxed "${BASE[@]}" --check
check "--check reports an outdated helper, changes nothing" \
	'[[ ${RC} -eq 0 && "$(sha256sum <"${INPUT}/__main__.py")" == "${helper_edited}" && ! -e "${INPUT_BAK}" ]] &&
	 grep -qxF "Ayudante jarvis-input desactualizado: ${INPUT} (install.sh lo reemplaza y guarda el anterior en ${INPUT_BAK})" <<<"${OUT}"'
sandboxed "${BASE[@]}" --dry-run
check "dry-run on a changed helper announces the backup only" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "[dry-run] copia del ayudante anterior en ${INPUT_BAK}" <<<"${OUT}" &&
	 [[ "$(sha256sum <"${INPUT}/__main__.py")" == "${helper_edited}" && ! -e "${INPUT_BAK}" && ! -s "${CALLS}" ]]'
sandboxed "${BASE[@]}"
check "changed helper without --enable: backup, exact copy, hint, no systemctl" \
	'[[ ${RC} -eq 0 && ! -s "${CALLS}" ]] && grep -qxF "${INPUT_HINT}" <<<"${OUT}" &&
	 [[ "$(sha256sum <"${INPUT_BAK}/__main__.py")" == "${helper_edited}" && -f "${INPUT_BAK}/planted.py" ]] &&
	 [[ ! -e "${INPUT}/planted.py" ]] && cmp -s "${INPUT_SRC}/__main__.py" "${INPUT}/__main__.py" &&
	 grep -qxF "Copia del ayudante anterior: ${INPUT_BAK}" <<<"${OUT}" && [[ -z "$(find "${INPUT_ROOT}" -name "*.new.*")" ]]'
printf '# again\n' >>"${INPUT}/sub/x.py"
reset_calls
sandboxed "${BASE[@]}" --enable
check "--enable after a helper change try-restarts only the helper" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user try-restart jarvis-input.service\n--user enable --now jarvis-input.service\n--user enable --now gentle-dot.service'"'"' ]] &&
	 cmp -s "${INPUT_SRC}/sub/x.py" "${INPUT}/sub/x.py" && grep -q "^# again" "${INPUT_BAK}/sub/x.py"'
printf '# stale\n' >>"${INPUT_UNIT}"
reset_calls
sandboxed "${BASE[@]}" --enable
check "--enable after a helper unit change reloads, try-restarts the helper" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart jarvis-input.service\n--user enable --now jarvis-input.service\n--user enable --now gentle-dot.service'"'"' ]] &&
	 cmp -s "${HERE}/jarvis-input.service.in" "${INPUT_UNIT}" && grep -qxF "# stale" "${INPUT_UNIT}.bak"'
# A read-only path that went missing is created again before any (re)start.
rm -rf -- "${APPS}"
reset_calls
sandboxed "${BASE[@]}" --enable
check "install recreates a missing read-only path before enabling" '[[ ${RC} -eq 0 && -d "${APPS}" ]]'
reset_calls
INPUT_SRC_OVERRIDE="${SANDBOX}/no-such-helper" sandboxed "${BASE[@]}"
check "refuses when the helper package is missing, calls nothing" \
	'[[ ${RC} -ne 0 && ! -s "${CALLS}" ]] && grep -qF "falta el ayudante ${SANDBOX}/no-such-helper" <<<"${OUT}"'
mkdir -p "${SANDBOX}/input-nomain/jarvis_input" && : >"${SANDBOX}/input-nomain/jarvis_input/__init__.py"
INPUT_SRC_OVERRIDE="${SANDBOX}/input-nomain/jarvis_input" sandboxed "${BASE[@]}" --check
check "refuses a helper package without __main__.py" '[[ ${RC} -ne 0 ]] && grep -qF "__main__.py" <<<"${OUT}"'

# 5b. --enable on a stale unit: rewrite, reload, restart if running, enable --now.
printf '# stale\n' >>"${UNIT}"
reset_calls
sandboxed "${BASE[@]}" --enable
check "--enable on a stale unit rewrites it" '[[ ${RC} -eq 0 && "$(sha256sum "${UNIT}")" == "${before}" ]]'
check "--enable on a stale unit reloads, try-restarts, enables" \
	'[[ "$(calls)" == $'"'"'--user daemon-reload\n--user enable --now jarvis-input.service\n--user try-restart gentle-dot.service\n--user enable --now gentle-dot.service'"'"' ]]'

# 5c. Start limit: the rendered unit caps restarts in its [Unit] section.
unit_section="$(awk '/^\[/{s=$0; next} s=="[Unit]"' "${UNIT}")"
check "unit pins StartLimitIntervalSec=10min in [Unit]" 'grep -qxF "StartLimitIntervalSec=10min" <<<"${unit_section}"'
check "unit pins StartLimitBurst=10 in [Unit]" 'grep -qxF "StartLimitBurst=10" <<<"${unit_section}"'

# 5g. Graphical session: the unit starts with the session, after the compositor has put
# WAYLAND_DISPLAY/DISPLAY into the user manager, and stops with it (not default.target).
check "unit orders After=graphical-session.target in [Unit]" 'grep -qxF "After=graphical-session.target" <<<"${unit_section}"'
check "unit is PartOf=graphical-session.target in [Unit]" 'grep -qxF "PartOf=graphical-session.target" <<<"${unit_section}"'
install_section="$(awk '/^\[/{s=$0; next} s=="[Install]"' "${UNIT}")"
check "unit is WantedBy=graphical-session.target only" \
	'[[ "$(grep -E "^WantedBy=" <<<"${install_section}")" == "WantedBy=graphical-session.target" ]]'

# Upgrade from a unit enabled under default.target: reenable drops the stale
# default.target.wants link (the stub cannot remove it, so it stays for the next cases).
readonly CFG_UP="${SANDBOX}/config-upgrade"
readonly UNIT_UP="${CFG_UP}/systemd/user/gentle-dot.service"
mkdir -p "${CFG_UP}/systemd/user/default.target.wants"
sed -e '/graphical-session.target/d' -e '/^\[Install\]/a WantedBy=default.target' "${UNIT}" >"${UNIT_UP}"
ln -s ../gentle-dot.service "${CFG_UP}/systemd/user/default.target.wants/gentle-dot.service"
reenable_msg="La unidad seguía habilitada en default.target; se vuelve a habilitar en graphical-session.target."
reset_calls
CFG="${CFG_UP}" sandboxed "${BASE[@]}" --enable
# Same unit as the default install except the rendered unit directory (ReadOnlyPaths=).
check "upgrade from default.target rewrites the unit" \
	'[[ ${RC} -eq 0 && "$(sed "s|${CFG_UP}/|${FAKE_CONFIG}/|g" "${UNIT_UP}" | sha256sum)" == "$(sha256sum <"${UNIT}")" ]]'
check "upgrade from default.target reloads, try-restarts, reenables, starts" \
	'[[ "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart jarvis-input.service\n--user enable --now jarvis-input.service\n--user try-restart gentle-dot.service\n--user reenable gentle-dot.service\n--user start gentle-dot.service'"'"' ]] && grep -qxF "${reenable_msg}" <<<"${OUT}"'
reset_calls
CFG="${CFG_UP}" sandboxed "${BASE[@]}" --enable
check "--enable on an unchanged unit with a stale link only reenables and starts" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user enable --now jarvis-input.service\n--user reenable gentle-dot.service\n--user start gentle-dot.service'"'"' ]]'
reset_calls
CFG="${CFG_UP}" sandboxed "${BASE[@]}"
check "install without --enable warns about a stale default.target link, calls nothing" \
	'[[ ${RC} -eq 0 && ! -s "${CALLS}" ]] && grep -qxF "aviso: la unidad sigue habilitada en default.target; para pasarla a graphical-session.target: systemctl --user reenable gentle-dot.service (o este script con --enable)." <<<"${OUT}"'
check "default install does not suggest linger" '! grep -q "enable-linger" <<<"${OUT}"'

# --headless: for a PC without a graphical session, back to default.target.
reset_calls
sandboxed "${BASE[@]}" --headless --dry-run
check "--headless renders WantedBy=default.target and no session binding" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "    | WantedBy=default.target" <<<"${OUT}" && ! grep -q "graphical-session.target" <<<"${OUT}" && [[ ! -s "${CALLS}" ]]'
check "--headless suggests linger" 'grep -q "enable-linger" <<<"${OUT}"'
readonly CFG_HL="${SANDBOX}/config-headless"
readonly UNIT_HL="${CFG_HL}/systemd/user/gentle-dot.service"
mkdir -p "${CFG_HL}/systemd/user/graphical-session.target.wants"
ln -s ../gentle-dot.service "${CFG_HL}/systemd/user/graphical-session.target.wants/gentle-dot.service"
reset_calls
CFG="${CFG_HL}" sandboxed "${BASE[@]}" --headless --enable
check "--headless --enable over a graphical-session link reenables and starts, leaves the helper off" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart gentle-dot.service\n--user reenable gentle-dot.service\n--user start gentle-dot.service'"'"' ]] &&
	 grep -qxF "Sin sesión gráfica (--headless): jarvis-input.service queda instalado pero no se habilita." <<<"${OUT}"'
check "--headless unit has WantedBy=default.target" 'grep -qxF "WantedBy=default.target" "${UNIT_HL}" && ! grep -q "graphical-session.target" "${UNIT_HL}"'

readonly BAK="${UNIT}.bak"
readonly PENDING="${UNIT}.reload-pending"

# 5d. Backup: an existing, different unit is copied to .bak before it is overwritten.
check "5b kept the stale unit as backup" '[[ -f "${BAK}" ]] && grep -qxF "# stale" "${BAK}"'
bak_prev="$(sha256sum <"${BAK}")"
printf '# local edit\n' >>"${UNIT}"
edited="$(sha256sum <"${UNIT}")"
reset_calls
sandboxed "${BASE[@]}" --dry-run
check "dry-run on a different unit announces the backup" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "[dry-run] copia de la unidad anterior en ${BAK}" <<<"${OUT}"'
check "dry-run on a different unit writes no backup and keeps the unit" \
	'[[ "$(sha256sum <"${BAK}")" == "${bak_prev}" && "$(sha256sum <"${UNIT}")" == "${edited}" && ! -s "${CALLS}" ]]'
sandboxed "${BASE[@]}"
check "install over a different unit exits 0 and says where the backup is" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "Copia de la unidad anterior: ${BAK}" <<<"${OUT}"'
check "backup holds the previous unit" '[[ "$(sha256sum <"${BAK}")" == "${edited}" ]]'
check "unit is rewritten after the backup" '[[ "$(sha256sum "${UNIT}")" == "${before}" ]]'
check "backup is 0644" '[[ "$(stat -c %a "${BAK}")" == 644 ]]'

# 5e. A failed daemon-reload is retried by the next run instead of "Sin cambios".
printf '# stale\n' >>"${UNIT}"
touch "${FAIL_RELOAD}"
reset_calls
sandboxed "${BASE[@]}" --enable
check "failed daemon-reload exits non-zero with a retry hint" \
	'[[ ${RC} -ne 0 ]] && grep -qxF "error: systemctl --user daemon-reload falló; ${UNIT} quedó escrita pero systemd no la cargó. Vuelve a ejecutar este script para reintentar la recarga." <<<"${OUT}"'
check "failed daemon-reload stops before enabling" '[[ "$(calls)" == "--user daemon-reload" ]]'
check "failed daemon-reload leaves the new unit and a pending marker" \
	'[[ "$(sha256sum "${UNIT}")" == "${before}" && -e "${PENDING}" ]]'
reset_calls
sandboxed "${BASE[@]}" --enable
check "rerun after a failed reload exits 0 and says it retries" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "Recarga pendiente: un daemon-reload anterior falló; se reintenta." <<<"${OUT}" && ! grep -qF "Sin cambios: ${UNIT}" <<<"${OUT}"'
check "rerun after a failed reload reloads, try-restarts both, enables" \
	'[[ "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart jarvis-input.service\n--user enable --now jarvis-input.service\n--user try-restart gentle-dot.service\n--user enable --now gentle-dot.service'"'"' ]]'
check "successful retry clears the marker" '[[ ! -e "${PENDING}" ]]'
reset_calls
sandboxed "${BASE[@]}"
check "after the retry the next run has no changes" \
	'[[ ${RC} -eq 0 && ! -s "${CALLS}" ]] && grep -qF "Sin cambios" <<<"${OUT}"'

# 5h. Agent rules: a different AGENTS.md (user edits) is backed up, then replaced.
printf '# my edit\n' >>"${RULES}"
rules_edited="$(sha256sum <"${RULES}")"
reset_calls
sandboxed "${BASE[@]}" --check
check "--check reports outdated agent rules and changes nothing" \
	'[[ ${RC} -eq 0 && "$(sha256sum <"${RULES}")" == "${rules_edited}" && ! -e "${RULES_BAK}" ]] &&
	 grep -qxF "Reglas del agente desactualizadas: ${RULES} (install.sh las reemplaza y guarda la anterior en ${RULES_BAK})" <<<"${OUT}"'
sandboxed "${BASE[@]}" --dry-run
check "dry-run on different agent rules announces backup and write only" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "[dry-run] copia de las reglas anteriores en ${RULES_BAK}" <<<"${OUT}" &&
	 grep -qxF "[dry-run] escribir ${RULES} (desde ${RULES_SRC})" <<<"${OUT}" &&
	 [[ "$(sha256sum <"${RULES}")" == "${rules_edited}" && ! -e "${RULES_BAK}" && ! -s "${CALLS}" ]]'
sandboxed "${BASE[@]}"
check "install over different agent rules says where the backup is" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "Copia de las reglas anteriores: ${RULES_BAK}" <<<"${OUT}"'
check "agent rules backup holds the edited file, 0644" '[[ "$(sha256sum <"${RULES_BAK}")" == "${rules_edited}" && "$(stat -c %a "${RULES_BAK}")" == 644 ]]'
check "agent rules are replaced by the shipped ones" 'cmp -s "${RULES_SRC}" "${RULES}"'
check "changed agent rules without --enable: hint, no systemctl" '[[ ! -s "${CALLS}" ]] && grep -qxF "${RESTART_HINT}" <<<"${OUT}"'
printf '# again\n' >>"${RULES}"
reset_calls
sandboxed "${BASE[@]}" --enable
check "--enable after an agent rules change try-restarts, then enables" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user enable --now jarvis-input.service\n--user try-restart gentle-dot.service\n--user enable --now gentle-dot.service'"'"' ]] &&
	 cmp -s "${RULES_SRC}" "${RULES}" && ! grep -qxF "${RESTART_HINT}" <<<"${OUT}"'
# 5i. Extension and stop: an edited copy is backed up (the extension outside extensions/, so the
# engine never loads the backup), then replaced; --enable try-restarts for the extension.
printf '// my edit\n' >>"${EXT}/pc-control-core.ts"
printf '# my edit\n' >>"${STOP}"
ext_edited="$(sha256sum <"${EXT}/pc-control-core.ts")"
stop_edited="$(sha256sum <"${STOP}")"
reset_calls
sandboxed "${BASE[@]}" --check
check "--check reports the outdated extension and stop, changes nothing" \
	'[[ ${RC} -eq 0 && "$(sha256sum <"${EXT}/pc-control-core.ts")" == "${ext_edited}" && ! -e "${EXT_BAK}" ]] &&
	 grep -qxF "Extensión de control de la PC desactualizada: ${EXT} (install.sh la reemplaza y guarda la anterior en ${EXT_BAK})" <<<"${OUT}" &&
	 grep -qxF "Parada desactualizada: ${STOP} (install.sh la reemplaza y guarda la anterior en ${STOP}.bak)" <<<"${OUT}"'
sandboxed "${BASE[@]}" --dry-run
check "dry-run on a different extension and stop announces the backups only" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "[dry-run] copia de la extensión anterior en ${EXT_BAK}" <<<"${OUT}" &&
	 grep -qxF "[dry-run] copia de la parada anterior en ${STOP}.bak" <<<"${OUT}" &&
	 [[ "$(sha256sum <"${EXT}/pc-control-core.ts")" == "${ext_edited}" && ! -e "${EXT_BAK}" && ! -e "${STOP}.bak" && ! -s "${CALLS}" ]]'
sandboxed "${BASE[@]}" --enable
check "--enable after an extension change backs up, replaces, try-restarts, enables" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user enable --now jarvis-input.service\n--user try-restart gentle-dot.service\n--user enable --now gentle-dot.service'"'"' ]] &&
	 cmp -s "${EXT_SRC}/pc-control-core.ts" "${EXT}/pc-control-core.ts" && cmp -s "${STOP_SRC}" "${STOP}" &&
	 grep -qxF "Copia de la extensión anterior: ${EXT_BAK}" <<<"${OUT}" && grep -qxF "Copia de la parada anterior: ${STOP}.bak" <<<"${OUT}"'
check "extension backup holds the edited files outside extensions/" \
	'[[ "$(sha256sum <"${EXT_BAK}/pc-control-core.ts.bak")" == "${ext_edited}" && -f "${EXT_BAK}/index.ts.bak" ]] &&
	 [[ -z "$(find "${FAKE_HOME}/.gentle-dot/agent/extensions" -name "*.bak" -o -name "*.tmp*")" ]]'
check "stop backup holds the edited script and is not executable" \
	'[[ "$(sha256sum <"${STOP}.bak")" == "${stop_edited}" && "$(stat -c %a "${STOP}.bak")" == 644 ]]'
mkdir -p "${SANDBOX}/ext-file/extensions" && : >"${SANDBOX}/ext-file/extensions/jarvis-pc-control"
sandboxed "${BASE[@]}" --agent-home "${SANDBOX}/ext-file"
check "refuses an extension path that is a file" '[[ ${RC} -ne 0 ]] && grep -q "no es una carpeta" <<<"${OUT}"'
sandboxed "${BASE[@]}" --bin-dir relative/bin --dry-run
check "refuses a relative bin dir" '[[ ${RC} -ne 0 ]] && grep -q "bin-dir" <<<"${OUT}"'
reset_calls
sandboxed "${BASE[@]}" --bin-dir "${SANDBOX}/bin-opt"
check "--bin-dir installs jarvis-stop there" '[[ ${RC} -eq 0 ]] && cmp -s "${STOP_SRC}" "${SANDBOX}/bin-opt/jarvis-stop"'
mkdir -p "${SANDBOX}/agent-dir/AGENTS.md"
sandboxed "${BASE[@]}" --agent-home "${SANDBOX}/agent-dir"
check "refuses an AGENTS.md that is a directory" '[[ ${RC} -ne 0 ]] && grep -q "no es un archivo" <<<"${OUT}"'
reset_calls
sandboxed "${BASE[@]}" --agent-home "${SANDBOX}/agent-opt"
check "--agent-home installs the rules there" '[[ ${RC} -eq 0 ]] && cmp -s "${RULES_SRC}" "${SANDBOX}/agent-opt/AGENTS.md"'
set +e
OUT="$(env -i PATH="${STUBS}:/usr/bin:/bin" HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${FAKE_CONFIG}" \
	JARVIS_INPUT_SRC_DIR="${INPUT_SRC}" JARVIS_TUI_SRC_DIR="${TUI_SRC}" JARVIS_TUI_REQUIREMENTS="${TUI_REQ_SRC}" \
	JARVIS_TUI_PYTHON="${STUBS}/python3-tui" GENTLE_DOT_AGENT_HOME="${SANDBOX}/agent-env" bash "${INSTALL}" "${BASE[@]}" 2>&1)"
RC=$?
set -e
check "GENTLE_DOT_AGENT_HOME installs the rules there" '[[ ${RC} -eq 0 ]] && cmp -s "${RULES_SRC}" "${SANDBOX}/agent-env/AGENTS.md"'
sandboxed "${BASE[@]}" --agent-home relative/agent --dry-run
check "refuses a relative agent home" '[[ ${RC} -ne 0 ]] && grep -q "ruta absoluta" <<<"${OUT}"'

# 5f. Dirty checkout: refused unless --allow-dirty; a refusal changes nothing.
if command -v git >/dev/null 2>&1; then
	readonly GD_DIRTY="${SANDBOX}/gentle-dot-dirty"
	mkdir -p "${GD_DIRTY}/packages/daemon/src" "${GD_DIRTY}/node_modules"
	: >"${GD_DIRTY}/packages/daemon/src/cli.ts"
	printf 'node_modules/\n' >"${GD_DIRTY}/.gitignore"
	git -C "${GD_DIRTY}" init -q
	git -C "${GD_DIRTY}" add -A
	git -C "${GD_DIRTY}" -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q -m t
	DIRTY_ARGS=(--gentle-dot-dir "${GD_DIRTY}" --node "${STUBS}/node" --allow-unpinned)
	dirty_msg="error: el checkout ${GD_DIRTY} tiene cambios sin commitear (git -C ${GD_DIRTY} status); el servicio no correría el commit revisado. Haz commit o descártalos, o usa --allow-dirty."
	unit_before="$(sha256sum "${UNIT}")"
	bak_before="$(sha256sum "${BAK}")"

	sandboxed "${DIRTY_ARGS[@]}" --check
	check "a clean checkout passes --check" '[[ ${RC} -eq 0 ]]'

	printf '// edit\n' >>"${GD_DIRTY}/packages/daemon/src/cli.ts"
	reset_calls
	sandboxed "${DIRTY_ARGS[@]}" --enable
	check "refuses a checkout with uncommitted changes" '[[ ${RC} -ne 0 ]] && grep -qxF "${dirty_msg}" <<<"${OUT}"'
	check "dirty refusal leaves unit and backup, calls nothing" \
		'[[ "$(sha256sum "${UNIT}")" == "${unit_before}" && "$(sha256sum "${BAK}")" == "${bak_before}" && ! -e "${PENDING}" && ! -s "${CALLS}" ]]'
	sandboxed "${DIRTY_ARGS[@]}" --check
	check "--check also refuses a dirty checkout" '[[ ${RC} -ne 0 ]] && grep -qxF "${dirty_msg}" <<<"${OUT}"'

	git -C "${GD_DIRTY}" -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q -am t2
	: >"${GD_DIRTY}/untracked.txt"
	sandboxed "${DIRTY_ARGS[@]}"
	check "refuses a checkout with untracked files" '[[ ${RC} -ne 0 ]] && grep -qxF "${dirty_msg}" <<<"${OUT}"'
	check "untracked refusal leaves the unit, calls nothing" '[[ "$(sha256sum "${UNIT}")" == "${unit_before}" && ! -s "${CALLS}" ]]'

	sandboxed "${DIRTY_ARGS[@]}" --allow-dirty --dry-run
	check "--allow-dirty accepts it with a warning" \
		'[[ ${RC} -eq 0 ]] && grep -qxF "aviso: el checkout ${GD_DIRTY} tiene cambios sin commitear; se sigue por --allow-dirty" <<<"${OUT}"'
	check "--allow-dirty --dry-run changes nothing" '[[ "$(sha256sum "${UNIT}")" == "${unit_before}" && ! -s "${CALLS}" ]]'
fi

sandboxed --help
check "help documents --allow-dirty" '[[ ${RC} -eq 0 ]] && grep -qE "^  --allow-dirty +" <<<"${OUT}"'
check "help documents --headless" 'grep -qE "^  --headless +" <<<"${OUT}"'
check "help documents --agent-home" 'grep -qE "^  --agent-home DIR +" <<<"${OUT}"'
check "help documents --bin-dir" 'grep -qE "^  --bin-dir DIR +" <<<"${OUT}"'

# 6. Dry-run uninstall: prints, removes nothing, calls nothing.
reset_calls
sandboxed --uninstall --dry-run
check "dry-run uninstall exits 0" '[[ ${RC} -eq 0 ]]'
check "dry-run uninstall shows disable, rm, reload" \
	'grep -qF "[dry-run] systemctl --user disable --now gentle-dot.service" <<<"${OUT}" &&
	 grep -qF "[dry-run] rm -f -- ${UNIT}" <<<"${OUT}" &&
	 grep -qF "[dry-run] systemctl --user daemon-reload" <<<"${OUT}"'
check "dry-run uninstall keeps the unit" '[[ -f "${UNIT}" && -f "${INPUT_UNIT}" && ! -s "${CALLS}" ]]'
check "dry-run uninstall shows the helper unit removal" \
	'grep -qF "[dry-run] systemctl --user disable --now jarvis-input.service" <<<"${OUT}" && grep -qF "[dry-run] rm -f -- ${INPUT_UNIT}" <<<"${OUT}"'
keep_msg="Se conservan las reglas del agente en ${RULES} (pueden tener cambios tuyos)."
check "dry-run uninstall keeps the agent rules" 'cmp -s "${RULES_SRC}" "${RULES}" && grep -qxF "${keep_msg}" <<<"${OUT}"'

# 7. Uninstall, then again (idempotent).
sandboxed --uninstall
check "uninstall exits 0" '[[ ${RC} -eq 0 ]]'
check "uninstall removes the unit" '[[ ! -e "${UNIT}" ]]'
check "uninstall keeps the agent rules and says so" 'cmp -s "${RULES_SRC}" "${RULES}" && grep -qxF "${keep_msg}" <<<"${OUT}"'
check "uninstall keeps the extension and the stop and says so" \
	'[[ -f "${EXT}/index.ts" && -f "${STOP}" ]] && grep -qxF "Se conserva la extensión de control de la PC en ${EXT} (sin /control on no hace nada)." <<<"${OUT}" && grep -qxF "Se conserva la parada ${STOP}." <<<"${OUT}"'
check "uninstall disables, reloads, resets both units" \
	'[[ "$(calls)" == $'"'"'--user disable --now gentle-dot.service\n--user disable --now jarvis-input.service\n--user daemon-reload\n--user reset-failed gentle-dot.service jarvis-input.service'"'"' ]]'
check "uninstall removes the helper unit, keeps the helper files and says so" \
	'[[ ! -e "${INPUT_UNIT}" && -f "${INPUT}/__main__.py" && -f "${SBX}" ]] && grep -qxF "Se conserva el ayudante en ${INPUT_ROOT} (sin su servicio no corre)." <<<"${OUT}"'
check "uninstall keeps the TUI, its launcher and the work folder, and says so" \
	'[[ -x "${TUI_LAUNCHER}" && -f "${TUI}/tui.py" && -d "${WORK}" ]] && grep -qxF "Se conserva la TUI en ${TUI_ROOT} y su lanzador ${TUI_LAUNCHER}." <<<"${OUT}"'
reset_calls
sandboxed --uninstall
check "second uninstall is a no-op" '[[ ${RC} -eq 0 && ! -s "${CALLS}" ]] && grep -q "nada que desinstalar" <<<"${OUT}"'

# 7b. A failed daemon-reload during uninstall is retried by the next --uninstall.
sandboxed "${BASE[@]}"
touch "${FAIL_RELOAD}"
reset_calls
sandboxed --uninstall
check "uninstall with a failed reload exits non-zero with a retry hint" \
	'[[ ${RC} -ne 0 ]] && grep -qxF "error: systemctl --user daemon-reload falló; ${UNIT} ya se borró pero systemd no recargó. Vuelve a ejecutar --uninstall para reintentar la recarga." <<<"${OUT}"'
check "uninstall with a failed reload removes the unit, keeps a marker" '[[ ! -e "${UNIT}" && -e "${PENDING}" ]]'
reset_calls
sandboxed --uninstall
check "uninstall rerun retries the reload" \
	'[[ ${RC} -eq 0 ]] && grep -qxF "Recarga pendiente: un daemon-reload anterior falló; se reintenta." <<<"${OUT}" &&
	 [[ "$(calls)" == $'"'"'--user daemon-reload\n--user reset-failed gentle-dot.service jarvis-input.service'"'"' && ! -e "${PENDING}" ]]'
reset_calls
sandboxed --uninstall
check "uninstall after the retry is a no-op" '[[ ${RC} -eq 0 && ! -s "${CALLS}" ]] && grep -q "nada que desinstalar" <<<"${OUT}"'

# 8. Refusals.
set +e
OUT="$(env -i PATH="${SANDBOX}/rootbin:${STUBS}:/usr/bin:/bin" HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${FAKE_CONFIG}" \
	bash "${INSTALL}" "${BASE[@]}" 2>&1)"
RC=$?
set -e
check "refuses to run as root" '[[ ${RC} -ne 0 ]] && grep -q "root" <<<"${OUT}" && [[ ! -e "${UNIT}" && ! -s "${CALLS}" ]]'
OUT="$(env -i PATH="${SANDBOX}/rootbin:${STUBS}:/usr/bin:/bin" HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${FAKE_CONFIG}" \
	bash "${INSTALL}" --uninstall 2>&1)" && RC=0 || RC=$?
check "refuses to uninstall as root" '[[ ${RC} -ne 0 ]] && grep -q "root" <<<"${OUT}"'

sandboxed --gentle-dot-dir "${GD}" --node "${STUBS}/node20" --allow-unpinned
check "refuses Node older than 24" '[[ ${RC} -ne 0 ]] && grep -q "Node 24" <<<"${OUT}"'

sandboxed --gentle-dot-dir "${GD}" --node "${STUBS}/node"
check "refuses a checkout it cannot pin without --allow-unpinned" '[[ ${RC} -ne 0 ]] && grep -q "commit fijado" <<<"${OUT}"'

if command -v git >/dev/null 2>&1; then
	readonly GD_GIT="${SANDBOX}/gentle-dot-git"
	mkdir -p "${GD_GIT}/packages/daemon/src" "${GD_GIT}/node_modules"
	: >"${GD_GIT}/packages/daemon/src/cli.ts"
	git -C "${GD_GIT}" init -q
	git -C "${GD_GIT}" -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --allow-empty -m t
	sandboxed --gentle-dot-dir "${GD_GIT}" --node "${STUBS}/node"
	check "refuses a checkout on another commit" '[[ ${RC} -ne 0 ]] && grep -q "no en el commit fijado f97d56d" <<<"${OUT}"'
fi

mkdir -p "${SANDBOX}/with space/packages/daemon/src"
sandboxed --gentle-dot-dir "${SANDBOX}/with space" --node "${STUBS}/node" --allow-unpinned
check "refuses a path the unit cannot hold verbatim" '[[ ${RC} -ne 0 ]] && grep -q "caracteres no admitidos" <<<"${OUT}"'

mkdir -p "${SANDBOX}/shims" && cp "${STUBS}/node" "${SANDBOX}/shims/node"
sandboxed --gentle-dot-dir "${GD}" --node "${SANDBOX}/shims/node" --allow-unpinned
check "refuses a version-manager shim" '[[ ${RC} -ne 0 ]] && grep -q "shim" <<<"${OUT}"'

mkdir -p "${SANDBOX}/gentle-dot-bare/packages/daemon/src"
: >"${SANDBOX}/gentle-dot-bare/packages/daemon/src/cli.ts"
sandboxed --gentle-dot-dir "${SANDBOX}/gentle-dot-bare" --node "${STUBS}/node" --allow-unpinned
check "refuses a checkout without pnpm install" '[[ ${RC} -ne 0 ]] && grep -q "pnpm install" <<<"${OUT}"'
check "no refusal wrote a unit or called systemctl" '[[ ! -e "${UNIT}" && ! -s "${CALLS}" ]]'

# 9. Unit syntax, when systemd-analyze is available (it reads the file only).
if command -v systemd-analyze >/dev/null 2>&1; then
	sandboxed "${BASE[@]}"
	set +e
	VERIFY="$(env HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${FAKE_CONFIG}" systemd-analyze --user verify "${UNIT}" 2>&1)"
	VRC=$?
	set -e
	OUT="${VERIFY}"
	check "systemd-analyze --user verify accepts the unit" '[[ ${VRC} -eq 0 && -z "${VERIFY}" ]]'
	set +e
	VERIFY="$(env HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${CFG_HL}" systemd-analyze --user verify "${UNIT_HL}" 2>&1)"
	VRC=$?
	set -e
	OUT="${VERIFY}"
	check "systemd-analyze --user verify accepts the --headless unit" '[[ ${VRC} -eq 0 && -z "${VERIFY}" ]]'
	set +e
	VERIFY="$(env HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${FAKE_CONFIG}" systemd-analyze --user verify "${INPUT_UNIT}" 2>&1)"
	VRC=$?
	set -e
	OUT="${VERIFY}"
	check "systemd-analyze --user verify accepts jarvis-input.service" '[[ ${VRC} -eq 0 && -z "${VERIFY}" ]]'
else
	printf 'skip systemd-analyze not installed\n'
fi

# 10. The Landlock wrapper: refuses relative programs; when the kernel has Landlock scopes,
# a child cannot reach an abstract socket made outside it, while plain runs can.
set +e
OUT="$(python3 -I "${SBX_SRC}" relative/prog 2>&1)"; RC=$?
set -e
check "sandbox wrapper refuses a relative program" '[[ ${RC} -ne 0 ]] && grep -q "ruta absoluta" <<<"${OUT}"'
readonly ABSTRACT_PROBE="${SANDBOX}/abstract_probe.py"
cat >"${ABSTRACT_PROBE}" <<'EOF'
import socket, sys
s = socket.socket(socket.AF_UNIX)
try:
    s.connect("\0" + sys.argv[1]); print("connected")
except OSError as e:
    print("blocked", e.errno)
EOF
abstract_name="jarvis-t21-test-$$"
set +e
python3 -I - "${abstract_name}" "${ABSTRACT_PROBE}" "${SBX_SRC}" >"${SANDBOX}/landlock.out" 2>&1 <<'EOF'
import socket, subprocess, sys
name, probe, wrapper = sys.argv[1:4]
srv = socket.socket(socket.AF_UNIX); srv.bind("\0" + name); srv.listen(4)
plain = subprocess.run([sys.executable, "-I", probe, name], capture_output=True, text=True).stdout.strip()
wrapped = subprocess.run([sys.executable, "-I", wrapper, sys.executable, "-I", probe, name], capture_output=True, text=True)
print("plain:", plain); print("wrapped:", wrapped.stdout.strip(), wrapped.returncode, wrapped.stderr.strip())
EOF
set -e
OUT="$(cat "${SANDBOX}/landlock.out")"
if grep -q "Landlock ABI" <<<"${OUT}"; then
	printf 'skip landlock scopes not available in this kernel\n'
else
	check "outside the wrapper the abstract socket answers" 'grep -qxF "plain: connected" <<<"${OUT}"'
	check "inside the wrapper the abstract socket is blocked (EPERM)" 'grep -qE "^wrapped: blocked 1 0" <<<"${OUT}"'
fi

printf '\n%d passed, %d failed\n' "${PASS}" "${FAIL}"
((FAIL == 0))
