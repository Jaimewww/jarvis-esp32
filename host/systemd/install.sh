#!/usr/bin/env bash
# Install, check or remove the Gentle Dot daemon as a systemd *user* service.
#
#   host/systemd/install.sh --gentle-dot-dir DIR [--node PATH] [--agent-home DIR] [--bin-dir DIR] [--headless] [--dry-run] [--enable]
#   host/systemd/install.sh --gentle-dot-dir DIR [--node PATH] [--agent-home DIR] [--bin-dir DIR] --check
#   host/systemd/install.sh --pi-bridge [--pi PATH] [--node PATH] [--agent-home DIR] [--bin-dir DIR] [--headless] [--dry-run|--check] [--enable]
#   host/systemd/install.sh --uninstall [--dry-run]
#
# It only writes ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gentle-dot.service, its
# backup gentle-dot.service.bak (the previous, different unit) and the marker
# gentle-dot.service.reload-pending (present while a daemon-reload is still owed), plus
# the agent rules host/gentle-dot/AGENTS.md as <agent home>/AGENTS.md (default
# ~/.gentle-dot/agent) and its backup AGENTS.md.bak, the pc-control extension
# (host/gentle-dot/extensions/) as <agent home>/extensions/jarvis-pc-control/ with its backup
# <agent home>/jarvis-pc-control.bak/ (outside extensions/, so the engine never loads it), and the
# stop key host/bin/jarvis-stop as ~/.local/bin/jarvis-stop (--bin-dir) with its backup
# jarvis-stop.bak; the jarvis-input helper (S13): its unit jarvis-input.service (+ .bak),
# the package host/input/jarvis_input/ as ~/.local/share/jarvis/input/jarvis_input/ (backup
# jarvis_input.bak/), the Landlock wrapper jarvis-sandbox-exec as
# ~/.local/share/jarvis/sandbox/jarvis-sandbox-exec (+ .bak) and, when missing, the empty
# directory ~/.local/share/applications; the TUI (S15): host/jarvis/ as
# ~/.local/share/jarvis/tui/jarvis/ (backup jarvis.bak/), host/requirements.txt beside it,
# its venv ~/.local/share/jarvis/tui/.venv (built with /usr/bin/python3 -m venv + pip; the
# previous one in .venv.bak; kept when pip fails) and the launcher ~/.local/bin/jarvis
# (--bin-dir; backup jarvis.bak); when missing, ~/.gentle-dot and the agent's work folder
# ~/Jarvis (0700; the daemon's only writable folders in a read-only home); and only calls
# `systemctl --user`.
# --pi-bridge (odd/tasks/pi-bridge.md S10) writes jarvis-pibridge.service (+ .bak) instead of
# gentle-dot.service, the same files otherwise, and ~/.pi (0700) when missing; it needs no
# Gentle Dot checkout. --enable disables and stops whichever of the two units it is not
# installing (both listen on 127.0.0.1:4317) and leaves its file in place; --uninstall
# removes both.
# It never runs as root, never enables linger, never installs, builds or downloads
# Gentle Dot, and touches nothing else under ~/.gentle-dot (data, token).
# See host/README.md, section "Daemon de Gentle Dot como servicio".
set -euo pipefail

readonly UNIT_NAME="gentle-dot.service"
# Pi bridge (S10): the alternative to gentle-dot.service, same port; --pi-bridge picks it.
readonly BRIDGE_UNIT_NAME="jarvis-pibridge.service"
# Upstream commit the decisions pin (docs/decisiones.md D1 and D8).
readonly PINNED_COMMIT="f97d56d884b1d3c178f415b95d486464e7b220bf"
readonly NODE_MIN_MAJOR=24
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly TEMPLATE="${SCRIPT_DIR}/${UNIT_NAME}.in"
readonly BRIDGE_TEMPLATE="${SCRIPT_DIR}/${BRIDGE_UNIT_NAME}.in"
# Rules the daemon's agent reads when the engine starts (e.g. launch GUI apps detached).
readonly RULES_SRC="$(cd -- "${SCRIPT_DIR}/.." && pwd)/gentle-dot/AGENTS.md"
# Jarvis PC control (S12): Pi extension (entry + pure core) and the emergency stop.
readonly EXT_SRC_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)/gentle-dot/extensions"
readonly EXT_NAME="jarvis-pc-control"
# source file in EXT_SRC_DIR -> installed name (the engine loads <dir>/index.ts).
readonly EXT_FILES=("pc-control.ts:index.ts" "pc-control-core.ts:pc-control-core.ts")
readonly STOP_SRC="$(cd -- "${SCRIPT_DIR}/.." && pwd)/bin/jarvis-stop"
# Jarvis input helper (S13): the only unit with the display. JARVIS_INPUT_SRC_DIR overrides
# the package source (tests, development).
readonly INPUT_UNIT_NAME="jarvis-input.service"
readonly INPUT_TEMPLATE="${SCRIPT_DIR}/${INPUT_UNIT_NAME}.in"
readonly INPUT_SRC_DIR="${JARVIS_INPUT_SRC_DIR:-$(cd -- "${SCRIPT_DIR}/.." && pwd)/input/jarvis_input}"
# Landlock wrapper the daemon starts through (abstract-socket and signal scopes).
readonly SBX_SRC="${SCRIPT_DIR}/jarvis-sandbox-exec"
# Jarvis TUI (S15): installed like the helper, out of the daemon's reach (its home is
# read-only). JARVIS_TUI_SRC_DIR, JARVIS_TUI_REQUIREMENTS and JARVIS_TUI_PYTHON (the base
# python that makes the venv) override the defaults (tests).
readonly TUI_SRC_DIR="${JARVIS_TUI_SRC_DIR:-$(cd -- "${SCRIPT_DIR}/.." && pwd)/jarvis}"
readonly TUI_REQ_SRC="${JARVIS_TUI_REQUIREMENTS:-$(cd -- "${SCRIPT_DIR}/.." && pwd)/requirements.txt}"
readonly TUI_PY="${JARVIS_TUI_PYTHON:-/usr/bin/python3}"
# Characters a unit file takes verbatim (anything else is refused, not escaped).
readonly UNIT_SAFE_RE='^[A-Za-z0-9._/+@-]+$'

usage() {
	cat <<'EOF'
Uso: install.sh [opciones]

  --gentle-dot-dir DIR  checkout de Gentle Dot (también por GENTLE_DOT_DIR)
  --node PATH           binario de Node 24+ (por defecto: `command -v node`)
  --agent-home DIR      carpeta del agente del daemon, donde van AGENTS.md y la extensión
                        (también por GENTLE_DOT_AGENT_HOME; por defecto ~/.gentle-dot/agent)
  --bin-dir DIR         donde va jarvis-stop (también por JARVIS_BIN_DIR; por defecto ~/.local/bin)
  --pi-bridge           instala el puente de Pi (jarvis-pibridge.service) en lugar de
                        Gentle Dot; no necesita --gentle-dot-dir. Gentle Dot sigue siendo
                        lo predeterminado: sin esta opción se instala Gentle Dot
  --pi PATH             binario de pi para --pi-bridge (por defecto: `command -v pi`)
  --enable              además habilita y arranca el servicio (enable --now); antes
                        detiene y deshabilita el otro (Gentle Dot o el puente)
  --headless            sin sesión gráfica: arranca con default.target en vez de
                        graphical-session.target (el agente no podrá abrir ventanas)
  --check               solo verifica los requisitos; no escribe ni llama a systemctl
  --dry-run             imprime lo que haría, sin escribir ni llamar a systemctl
  --uninstall           detiene, deshabilita y borra la unidad
  --allow-unpinned      acepta un checkout en otro commit (desaconsejado)
  --allow-dirty         acepta un checkout con cambios sin commitear (desaconsejado)
  -h, --help            esta ayuda

Antes de reemplazar una unidad distinta, guarda la anterior en gentle-dot.service.bak.
Si `systemctl --user daemon-reload` falla, deja gentle-dot.service.reload-pending y la
siguiente ejecución vuelve a recargar.
Instala host/gentle-dot/AGENTS.md (reglas del agente) en la carpeta del agente; si había
uno distinto, lo guarda en AGENTS.md.bak. --uninstall no lo borra (puede tener cambios tuyos).
Instala la extensión de control de la PC en <agente>/extensions/jarvis-pc-control/ (la anterior,
si era distinta, en <agente>/jarvis-pc-control.bak/) y la parada jarvis-stop en la carpeta de
--bin-dir (la anterior en jarvis-stop.bak). --uninstall tampoco los borra.
Instala el ayudante jarvis-input (host/input/jarvis_input/) en ~/.local/share/jarvis/input/,
su unidad jarvis-input.service y el envoltorio de aislamiento del daemon en
~/.local/share/jarvis/sandbox/; con --enable los habilita y arranca (el ayudante primero).
--uninstall quita jarvis-input.service y conserva los archivos del ayudante.
Instala la TUI en ~/.local/share/jarvis/tui/ con su propio entorno de Python (pip necesita red;
sin red conserva el entorno anterior) y el lanzador jarvis junto a jarvis-stop (el anterior en
jarvis.bak). Crea ~/Jarvis (0700): la carpeta de trabajo, lo único del home, además de
~/.gentle-dot, donde el daemon puede escribir.
Tras instalar, el aislamiento rige desde el siguiente arranque del daemon (--enable lo reinicia).
Con --pi-bridge escribe jarvis-pibridge.service (y su copia .bak) en lugar de gentle-dot.service
y crea ~/.pi (0700) si falta; el puente corre con el entorno de Python de la TUI. Gentle Dot y el
puente usan el mismo puerto: --enable detiene y deshabilita el otro y deja su unidad instalada,
así que volver es correr este script con la otra opción y --enable. --uninstall quita las dos.
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

DRY_RUN=0 ENABLE=0 CHECK=0 UNINSTALL=0 ALLOW_UNPINNED=0 ALLOW_DIRTY=0 HEADLESS=0 PI_BRIDGE=0
GD_DIR="${GENTLE_DOT_DIR:-}" NODE_BIN="" PI_BIN="" AGENT_HOME="${GENTLE_DOT_AGENT_HOME:-}" BIN_DIR="${JARVIS_BIN_DIR:-}"

while (($#)); do
	case "$1" in
	--gentle-dot-dir) [[ $# -ge 2 ]] || die "--gentle-dot-dir necesita un valor"; GD_DIR="$2"; shift 2 ;;
	--node) [[ $# -ge 2 ]] || die "--node necesita un valor"; NODE_BIN="$2"; shift 2 ;;
	--agent-home) [[ $# -ge 2 ]] || die "--agent-home necesita un valor"; AGENT_HOME="$2"; shift 2 ;;
	--bin-dir) [[ $# -ge 2 ]] || die "--bin-dir necesita un valor"; BIN_DIR="$2"; shift 2 ;;
	--enable) ENABLE=1; shift ;;
	--check) CHECK=1; shift ;;
	--dry-run) DRY_RUN=1; shift ;;
	--uninstall) UNINSTALL=1; shift ;;
	--allow-unpinned) ALLOW_UNPINNED=1; shift ;;
	--allow-dirty) ALLOW_DIRTY=1; shift ;;
	--headless) HEADLESS=1; shift ;;
	--pi-bridge) PI_BRIDGE=1; shift ;;
	--pi) [[ $# -ge 2 ]] || die "--pi necesita un valor"; PI_BIN="$2"; shift 2 ;;
	-h | --help) usage; exit 0 ;;
	*) usage >&2; die "opción desconocida: $1" ;;
	esac
done

((UNINSTALL && (ENABLE || CHECK))) && die "--uninstall no se combina con --enable ni --check"
# --pi names the binary of the bridge's agent: without --pi-bridge it would be dropped
# and Gentle Dot installed instead, so refuse it before anything is touched.
[[ -z "${PI_BIN}" ]] || ((PI_BRIDGE)) || die "--pi solo vale junto con --pi-bridge"

# --- Who and where -------------------------------------------------------------

[[ "$(id -u)" != "0" ]] || die "no se ejecuta como root: es un servicio de usuario (systemctl --user)"
[[ -n "${HOME:-}" && "${HOME}" == /* ]] || die "HOME no está definido o no es una ruta absoluta"
CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
[[ "${CONFIG_HOME}" == /* ]] || die "XDG_CONFIG_HOME debe ser una ruta absoluta"
readonly UNIT_DIR="${CONFIG_HOME}/systemd/user"
readonly UNIT_PATH="${UNIT_DIR}/${UNIT_NAME}"
readonly BACKUP_PATH="${UNIT_PATH}.bak"
# Exists while systemd has not loaded the last change: a later run reloads again.
readonly PENDING_PATH="${UNIT_PATH}.reload-pending"
readonly BRIDGE_UNIT_PATH="${UNIT_DIR}/${BRIDGE_UNIT_NAME}"
# The unit this run installs (SVC_*) and the other one on the same port (ALT_*). One reload
# marker for both: it means "a daemon-reload is owed", whichever unit changed.
if ((PI_BRIDGE)); then
	SVC_NAME="${BRIDGE_UNIT_NAME}" SVC_PATH="${BRIDGE_UNIT_PATH}" SVC_TEMPLATE="${BRIDGE_TEMPLATE}"
	ALT_NAME="${UNIT_NAME}" ALT_PATH="${UNIT_PATH}"
else
	SVC_NAME="${UNIT_NAME}" SVC_PATH="${UNIT_PATH}" SVC_TEMPLATE="${TEMPLATE}"
	ALT_NAME="${BRIDGE_UNIT_NAME}" ALT_PATH="${BRIDGE_UNIT_PATH}"
fi
readonly SVC_NAME SVC_PATH SVC_TEMPLATE ALT_NAME ALT_PATH
readonly SVC_BAK="${SVC_PATH}.bak"
AGENT_HOME="${AGENT_HOME:-${HOME}/.gentle-dot/agent}"
[[ "${AGENT_HOME}" == /* ]] || die "--agent-home (o GENTLE_DOT_AGENT_HOME) debe ser una ruta absoluta: ${AGENT_HOME}"
readonly AGENT_HOME
readonly RULES_PATH="${AGENT_HOME}/AGENTS.md"
readonly RULES_BAK="${RULES_PATH}.bak"
readonly EXT_DIR="${AGENT_HOME}/extensions/${EXT_NAME}"
readonly EXT_BAK="${AGENT_HOME}/${EXT_NAME}.bak"
BIN_DIR="${BIN_DIR:-${HOME}/.local/bin}"
[[ "${BIN_DIR}" == /* ]] || die "--bin-dir (o JARVIS_BIN_DIR) debe ser una ruta absoluta: ${BIN_DIR}"
readonly BIN_DIR
readonly STOP_PATH="${BIN_DIR}/jarvis-stop"
readonly STOP_BAK="${STOP_PATH}.bak"
readonly INPUT_UNIT_PATH="${UNIT_DIR}/${INPUT_UNIT_NAME}"
readonly INPUT_UNIT_BAK="${INPUT_UNIT_PATH}.bak"
readonly JARVIS_DATA="${HOME}/.local/share/jarvis"
readonly INPUT_ROOT="${JARVIS_DATA}/input"
readonly INPUT_DIR="${INPUT_ROOT}/jarvis_input"
readonly INPUT_BAK="${INPUT_ROOT}/jarvis_input.bak"
readonly SBX_DIR="${JARVIS_DATA}/sandbox"
readonly SBX_PATH="${SBX_DIR}/jarvis-sandbox-exec"
readonly SBX_BAK="${SBX_PATH}.bak"
readonly APPS_DIR="${HOME}/.local/share/applications"
# S15: the daemon's only writable folders in the home (ReadWritePaths=, no "-").
readonly GD_DATA="${HOME}/.gentle-dot"
readonly WORK_DIR="${HOME}/Jarvis"
# S10: Pi's own data, writable for the bridge only (ReadWritePaths=, no "-").
readonly PI_DATA="${HOME}/.pi"
readonly TUI_ROOT="${JARVIS_DATA}/tui"
readonly TUI_DIR="${TUI_ROOT}/jarvis"
readonly TUI_BAK="${TUI_ROOT}/jarvis.bak"
readonly TUI_VENV="${TUI_ROOT}/.venv"
readonly TUI_VENV_BAK="${TUI_VENV}.bak"
# Copy of the requirements the venv was built from: a different one means rebuild.
readonly TUI_STAMP="${TUI_VENV}/jarvis-requirements.txt"
readonly TUI_LAUNCHER="${BIN_DIR}/jarvis"
readonly TUI_LAUNCHER_BAK="${TUI_LAUNCHER}.bak"
readonly TUI_HINT="La TUI cambió: ciérrala y vuelve a abrirla con ${TUI_LAUNCHER}."
# Uninstall keeps the rules: the user may have edited them, and they are harmless alone.
# The extension stays too: without /control on in the TUI it refuses everything.
keep_rules() {
	[[ ! -e "${RULES_PATH}" ]] || say "Se conservan las reglas del agente en ${RULES_PATH} (pueden tener cambios tuyos)."
	[[ ! -e "${EXT_DIR}" ]] || say "Se conserva la extensión de control de la PC en ${EXT_DIR} (sin /control on no hace nada)."
	[[ ! -e "${STOP_PATH}" ]] || say "Se conserva la parada ${STOP_PATH}."
	[[ ! -e "${INPUT_ROOT}" ]] || say "Se conserva el ayudante en ${INPUT_ROOT} (sin su servicio no corre)."
	[[ ! -e "${TUI_ROOT}" ]] || say "Se conserva la TUI en ${TUI_ROOT} y su lanzador ${TUI_LAUNCHER}."
}

# run CMD...: execute, or only print it under --dry-run.
run() {
	if ((DRY_RUN)); then
		printf '[dry-run] %s\n' "$*"
	else
		printf '+ %s\n' "$*"
		"$@"
	fi
}

need_systemctl() {
	((DRY_RUN)) && return 0
	command -v systemctl >/dev/null 2>&1 || die "no se encontró systemctl"
}

# --- Uninstall -----------------------------------------------------------------

PENDING_MSG="Recarga pendiente: un daemon-reload anterior falló; se reintenta."

if ((UNINSTALL)); then
	if [[ ! -e "${UNIT_PATH}" && ! -e "${INPUT_UNIT_PATH}" && ! -e "${BRIDGE_UNIT_PATH}" && ! -e "${PENDING_PATH}" ]]; then
		say "No hay unidad en ${UNIT_PATH}; nada que desinstalar."
		keep_rules
		exit 0
	fi
	need_systemctl
	removed=0
	# The bridge's unit is reset too only when it was installed (S10).
	reset_units=("${UNIT_NAME}" "${INPUT_UNIT_NAME}")
	[[ ! -e "${BRIDGE_UNIT_PATH}" ]] || reset_units+=("${BRIDGE_UNIT_NAME}")
	for pair in "${UNIT_NAME}:${UNIT_PATH}" "${INPUT_UNIT_NAME}:${INPUT_UNIT_PATH}" "${BRIDGE_UNIT_NAME}:${BRIDGE_UNIT_PATH}"; do
		[[ -e "${pair#*:}" ]] || continue
		# disable --now fails when the manager no longer knows the unit; removal goes on.
		run systemctl --user disable --now "${pair%%:*}" || say "aviso: disable --now de ${pair%%:*} falló; se sigue"
		((DRY_RUN)) || : >"${PENDING_PATH}"
		run rm -f -- "${pair#*:}"
		removed=1
	done
	((removed)) || say "${PENDING_MSG}"
	run systemctl --user daemon-reload ||
		die "systemctl --user daemon-reload falló; ${UNIT_PATH} ya se borró pero systemd no recargó. Vuelve a ejecutar --uninstall para reintentar la recarga."
	((DRY_RUN)) || rm -f -- "${PENDING_PATH}"
	run systemctl --user reset-failed "${reset_units[@]}" 2>/dev/null || true
	[[ ! -e "${BACKUP_PATH}" ]] || say "Se conserva la copia ${BACKUP_PATH}."
	[[ ! -e "${BRIDGE_UNIT_PATH}.bak" ]] || say "Se conserva la copia ${BRIDGE_UNIT_PATH}.bak."
	[[ ! -e "${INPUT_UNIT_BAK}" ]] || say "Se conserva la copia ${INPUT_UNIT_BAK}."
	keep_rules
	say "Desinstalado. Los datos del daemon (~/.gentle-dot, con su token) no se tocaron."
	exit 0
fi

# --- Prerequisites -------------------------------------------------------------

if ((PI_BRIDGE)); then
	# S10: the bridge runs pi (a `#!/usr/bin/env node` script) with the TUI's Python; no
	# Gentle Dot checkout. Both binaries go into the unit verbatim, so they must be real
	# files, not version-manager shims (a service has no shell to pick the version).
	if [[ -z "${PI_BIN}" ]]; then
		PI_BIN="$(command -v pi 2>/dev/null)" || die "no se encontró pi; indica --pi /ruta/a/pi"
	fi
	if [[ -z "${NODE_BIN}" ]]; then
		NODE_BIN="$(command -v node 2>/dev/null)" || die "no se encontró node; indica --node /ruta/a/node"
	fi
	for pair in "--pi:${PI_BIN}" "--node:${NODE_BIN}"; do
		opt="${pair%%:*}" bin="${pair#*:}"
		[[ "${bin}" == /* ]] || die "${opt} debe ser una ruta absoluta: ${bin}"
		[[ -x "${bin}" && -f "${bin}" ]] || die "no es ejecutable: ${bin}"
		[[ "${bin}" =~ ${UNIT_SAFE_RE} ]] || die "ruta de ${opt#--} con caracteres no admitidos: ${bin}"
		case "${bin}" in
		*/shims/*) die "${bin} es un shim de un gestor de versiones; indica el binario real con ${opt}" ;;
		esac
	done
	node_version="$("${NODE_BIN}" --version 2>/dev/null)" || die "no se pudo ejecutar ${NODE_BIN} --version"
	# pi's and node's directories first: pi finds node through PATH.
	SEARCH_PATH="$(dirname -- "${PI_BIN}")"
	node_dir="$(dirname -- "${NODE_BIN}")"
	[[ "${node_dir}" == "${SEARCH_PATH}" ]] || SEARCH_PATH="${SEARCH_PATH}:${node_dir}"
	SEARCH_PATH="${SEARCH_PATH}:/usr/local/bin:/usr/bin"
	[[ -f "${TUI_SRC_DIR}/pibridge/__main__.py" ]] ||
		die "falta el puente ${TUI_SRC_DIR}/pibridge/__main__.py (paquete jarvis de host/, se arranca con python -m jarvis.pibridge)"
	[[ ! -e "${PI_DATA}" || -d "${PI_DATA}" ]] || die "${PI_DATA} existe pero no es una carpeta; muévelo"
else
	[[ -n "${GD_DIR}" ]] || die "falta --gentle-dot-dir (o GENTLE_DOT_DIR): la ruta del checkout de Gentle Dot"
	[[ -d "${GD_DIR}" ]] || die "no existe el directorio ${GD_DIR}"
	GD_DIR="$(cd -- "${GD_DIR}" && pwd -P)"
	# Characters that would need escaping in a unit file are refused instead.
	[[ "${GD_DIR}" =~ ^[A-Za-z0-9._/+@-]+$ ]] ||
		die "ruta de Gentle Dot con caracteres no admitidos (espacios, %, comillas…): ${GD_DIR}"
	readonly CLI="${GD_DIR}/packages/daemon/src/cli.ts"
	[[ -f "${CLI}" ]] || die "no es un checkout de Gentle Dot: falta ${CLI}"

	if [[ -e "${GD_DIR}/.git" ]]; then
		command -v git >/dev/null 2>&1 || die "hace falta git para comprobar el commit fijado"
		head_commit="$(git -C "${GD_DIR}" rev-parse HEAD 2>/dev/null)" || die "no se pudo leer el commit de ${GD_DIR}"
		if [[ "${head_commit}" != "${PINNED_COMMIT}" ]]; then
			((ALLOW_UNPINNED)) || die "el checkout está en ${head_commit}, no en el commit fijado ${PINNED_COMMIT} (git -C ${GD_DIR} switch --detach ${PINNED_COMMIT:0:7}; o --allow-unpinned)"
			say "aviso: checkout en ${head_commit}, no en el commit fijado ${PINNED_COMMIT}"
		fi
		# Uncommitted or untracked files mean the service would not run the reviewed commit.
		dirty="$(git -C "${GD_DIR}" status --porcelain 2>/dev/null)" || die "no se pudo leer git status de ${GD_DIR}"
		if [[ -n "${dirty}" ]]; then
			((ALLOW_DIRTY)) || die "el checkout ${GD_DIR} tiene cambios sin commitear (git -C ${GD_DIR} status); el servicio no correría el commit revisado. Haz commit o descártalos, o usa --allow-dirty."
			say "aviso: el checkout ${GD_DIR} tiene cambios sin commitear; se sigue por --allow-dirty"
		fi
	else
		((ALLOW_UNPINNED)) || die "${GD_DIR} no es un repositorio git; no se puede comprobar el commit fijado (o --allow-unpinned)"
		say "aviso: sin git en ${GD_DIR}; no se comprobó el commit fijado"
	fi
	[[ -d "${GD_DIR}/node_modules" ]] || die "faltan las dependencias de Gentle Dot: cd ${GD_DIR} && pnpm install --frozen-lockfile"
	[[ -d "${GD_DIR}/packages/ui/dist/app" ]] ||
		say "aviso: sin interfaz web compilada (packages/ui/dist/app); la TUI funciona igual. Para tenerla: cd ${GD_DIR} && pnpm build"

	if [[ -z "${NODE_BIN}" ]]; then
		NODE_BIN="$(command -v node 2>/dev/null)" || die "no se encontró node; indica --node /ruta/a/node (Node ${NODE_MIN_MAJOR}+)"
	fi
	[[ "${NODE_BIN}" == /* ]] || die "--node debe ser una ruta absoluta: ${NODE_BIN}"
	[[ -x "${NODE_BIN}" ]] || die "no es ejecutable: ${NODE_BIN}"
	[[ "${NODE_BIN}" =~ ^[A-Za-z0-9._/+@-]+$ ]] || die "ruta de node con caracteres no admitidos: ${NODE_BIN}"
	# A version-manager shim (mise, asdf, volta…) picks the version from the working
	# directory and the shell, which a service does not have: the real binary is required.
	case "${NODE_BIN}" in
	*/shims/*) die "${NODE_BIN} es un shim de un gestor de versiones; indica el binario real con --node (por ejemplo, \$(mise where node@24)/bin/node)" ;;
	esac
	node_version="$("${NODE_BIN}" --version 2>/dev/null)" || die "no se pudo ejecutar ${NODE_BIN} --version"
	node_major="${node_version#v}"
	node_major="${node_major%%.*}"
	[[ "${node_major}" =~ ^[0-9]+$ ]] || die "versión de node no reconocida: ${node_version}"
	((node_major >= NODE_MIN_MAJOR)) || die "Gentle Dot necesita Node ${NODE_MIN_MAJOR}+; ${NODE_BIN} es ${node_version}"
fi

[[ -f "${SVC_TEMPLATE}" ]] || die "falta la plantilla ${SVC_TEMPLATE}"
# Paths the sandboxed unit names verbatim (ReadOnlyPaths=, the wrapper).
# The TUI paths also go verbatim, quoted, into its sh launcher.
for p in "${UNIT_DIR}" "${STOP_PATH}" "${AGENT_HOME}" "${SBX_PATH}" "${TUI_LAUNCHER}" "${TUI_ROOT}" "${WORK_DIR}"; do
	[[ "${p}" =~ ${UNIT_SAFE_RE} ]] || die "ruta con caracteres no admitidos en una unidad (espacios, %, comillas…): ${p}"
done
if ((PI_BRIDGE)); then
	unit_text="$(sed -e "s|@PI@|${PI_BIN}|g" -e "s|@SEARCH_PATH@|${SEARCH_PATH}|g" -e "s|@TUI_VENV@|${TUI_VENV}|g" \
		-e "s|@TUI_ROOT@|${TUI_ROOT}|g" -e "s|@SANDBOX_EXEC@|${SBX_PATH}|g" \
		-e "s|@UNIT_DIR@|${UNIT_DIR}|g" -e "s|@STOP_PATH@|${STOP_PATH}|g" -e "s|@TUI_LAUNCHER@|${TUI_LAUNCHER}|g" \
		-e "s|@AGENT_HOME@|${AGENT_HOME}|g" "${SVC_TEMPLATE}")"
else
	unit_text="$(sed -e "s|@NODE@|${NODE_BIN}|g" -e "s|@GENTLE_DOT_DIR@|${GD_DIR}|g" -e "s|@SANDBOX_EXEC@|${SBX_PATH}|g" \
		-e "s|@UNIT_DIR@|${UNIT_DIR}|g" -e "s|@STOP_PATH@|${STOP_PATH}|g" -e "s|@TUI_LAUNCHER@|${TUI_LAUNCHER}|g" \
		-e "s|@AGENT_HOME@|${AGENT_HOME}|g" "${SVC_TEMPLATE}")"
fi
! grep -q '@[A-Z_]*@' <<<"${unit_text}" || die "la plantilla dejó variables sin reemplazar"
# The template starts with the graphical session; --headless installs into default.target.
WANTED_TARGET="graphical-session.target" OTHER_TARGET="default.target"
if ((HEADLESS)); then
	WANTED_TARGET="default.target" OTHER_TARGET="graphical-session.target"
	unit_text="$(sed -E -e '/^(After|PartOf)=graphical-session\.target$/d' \
		-e 's/^WantedBy=graphical-session\.target$/WantedBy=default.target/' <<<"${unit_text}")"
fi
grep -qxF "WantedBy=${WANTED_TARGET}" <<<"${unit_text}" || die "la plantilla no tiene WantedBy=${WANTED_TARGET}"
# A link left by an earlier install under the other target (e.g. default.target before the
# unit moved to the graphical session) keeps starting the service there: reenable drops it.
STALE_LINK="${UNIT_DIR}/${OTHER_TARGET}.wants/${SVC_NAME}"
stale=0
[[ -L "${STALE_LINK}" ]] && stale=1

# Agent rules: missing, current (byte-identical) or different (backed up, then replaced).
[[ -f "${RULES_SRC}" ]] || die "faltan las reglas del agente ${RULES_SRC}"
if [[ -e "${RULES_PATH}" && ! -f "${RULES_PATH}" ]]; then
	die "${RULES_PATH} existe pero no es un archivo; muévelo o indica otra carpeta con --agent-home"
fi
rules_state=missing
if [[ -f "${RULES_PATH}" ]]; then
	rules_state=different
	cmp -s -- "${RULES_SRC}" "${RULES_PATH}" && rules_state=current
fi

# PC-control extension: missing, current (every file byte-identical) or different.
for pair in "${EXT_FILES[@]}"; do
	[[ -f "${EXT_SRC_DIR}/${pair%%:*}" ]] || die "falta la extensión ${EXT_SRC_DIR}/${pair%%:*}"
done
if [[ -e "${EXT_DIR}" && ! -d "${EXT_DIR}" ]]; then
	die "${EXT_DIR} existe pero no es una carpeta; muévelo o indica otra carpeta con --agent-home"
fi
ext_state=missing
if [[ -d "${EXT_DIR}" ]]; then
	ext_state=current
	for pair in "${EXT_FILES[@]}"; do
		cmp -s -- "${EXT_SRC_DIR}/${pair%%:*}" "${EXT_DIR}/${pair#*:}" || ext_state=different
	done
fi
# Emergency stop script: same three states.
[[ -f "${STOP_SRC}" ]] || die "falta la parada ${STOP_SRC}"
if [[ -e "${STOP_PATH}" && ! -f "${STOP_PATH}" ]]; then
	die "${STOP_PATH} existe pero no es un archivo; muévelo o indica otra carpeta con --bin-dir"
fi
stop_state=missing
if [[ -f "${STOP_PATH}" ]]; then
	stop_state=different
	cmp -s -- "${STOP_SRC}" "${STOP_PATH}" && stop_state=current
fi

# jarvis-input helper: its unit (copied verbatim), its package and the daemon's wrapper.
[[ -f "${INPUT_TEMPLATE}" ]] || die "falta la plantilla ${INPUT_TEMPLATE}"
[[ -d "${INPUT_SRC_DIR}" ]] || die "falta el ayudante ${INPUT_SRC_DIR} (paquete jarvis_input de host/input/)"
[[ -f "${INPUT_SRC_DIR}/__main__.py" ]] || die "al ayudante ${INPUT_SRC_DIR} le falta __main__.py (se arranca con python3 -m jarvis_input)"
[[ -f "${SBX_SRC}" ]] || die "falta el envoltorio de aislamiento ${SBX_SRC}"
for p in "${INPUT_UNIT_PATH}" "${SBX_PATH}"; do
	[[ ! -e "${p}" || -f "${p}" ]] || die "${p} existe pero no es un archivo; muévelo"
done
for p in "${INPUT_DIR}" "${APPS_DIR}"; do
	[[ ! -e "${p}" || -d "${p}" ]] || die "${p} existe pero no es una carpeta; muévelo"
done
# file_state SRC DEST: missing, current (byte-identical) or different.
file_state() {
	[[ -f "$2" ]] || { echo missing; return; }
	if cmp -s -- "$1" "$2"; then echo current; else echo different; fi
}
# pkg_files DIR: the package's files, relative and sorted; bytecode caches do not count.
pkg_files() { (cd -- "$1" && find . -type d -name __pycache__ -prune -o ! -type d ! -name '*.pyc' -print | LC_ALL=C sort); }
input_unit_state="$(file_state "${INPUT_TEMPLATE}" "${INPUT_UNIT_PATH}")"
sbx_state="$(file_state "${SBX_SRC}" "${SBX_PATH}")"
input_files="$(pkg_files "${INPUT_SRC_DIR}")"
input_state=missing
if [[ -d "${INPUT_DIR}" ]]; then
	input_state=current
	if [[ "$(pkg_files "${INPUT_DIR}")" != "${input_files}" ]]; then
		input_state=different
	else
		while IFS= read -r rel; do
			cmp -s -- "${INPUT_SRC_DIR}/${rel}" "${INPUT_DIR}/${rel}" || { input_state=different; break; }
		done <<<"${input_files}"
	fi
fi

# TUI (S15): package (like the helper's), venv (rebuilt when the requirements change),
# launcher (rendered) and the work folder.
[[ -f "${TUI_SRC_DIR}/tui.py" ]] || die "falta la TUI ${TUI_SRC_DIR} (paquete jarvis de host/, con tui.py)"
[[ -f "${TUI_REQ_SRC}" ]] || die "faltan las dependencias de la TUI ${TUI_REQ_SRC}"
[[ "${TUI_PY}" == /* && -x "${TUI_PY}" ]] || die "no hay un python3 ejecutable en ${TUI_PY} para el entorno de la TUI"
for p in "${TUI_DIR}" "${TUI_VENV}" "${WORK_DIR}" "${GD_DATA}"; do
	[[ ! -e "${p}" || -d "${p}" ]] || die "${p} existe pero no es una carpeta; muévelo"
done
[[ ! -e "${TUI_LAUNCHER}" || -f "${TUI_LAUNCHER}" ]] || die "${TUI_LAUNCHER} existe pero no es un archivo; muévelo o indica otra carpeta con --bin-dir"
tui_files="$(pkg_files "${TUI_SRC_DIR}")"
tui_state=missing
if [[ -d "${TUI_DIR}" ]]; then
	tui_state=current
	if [[ "$(pkg_files "${TUI_DIR}")" != "${tui_files}" ]]; then
		tui_state=different
	else
		while IFS= read -r rel; do
			cmp -s -- "${TUI_SRC_DIR}/${rel}" "${TUI_DIR}/${rel}" || { tui_state=different; break; }
		done <<<"${tui_files}"
	fi
fi
venv_state=missing
if [[ -d "${TUI_VENV}" ]]; then
	venv_state=different
	[[ -x "${TUI_VENV}/bin/python" ]] && cmp -s -- "${TUI_REQ_SRC}" "${TUI_STAMP}" && venv_state=current
fi
# The launcher runs the installed copy with the venv's python: -I ignores PYTHON* variables, the
# user site and the current directory, so the copy's directory is put on sys.path by hand.
launcher_text="$(cat <<EOF
#!/bin/sh
# Jarvis TUI, written by host/systemd/install.sh (S15). Do not edit: the next run replaces
# it and keeps the previous one in $(basename -- "${TUI_LAUNCHER_BAK}"). It runs the installed copy in
# ${TUI_ROOT} with its own venv, which the Gentle Dot daemon cannot
# modify (read-only home). Extra arguments go to jarvis.tui (e.g. jarvis --theme paper).
exec "${TUI_VENV}/bin/python" -I -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("jarvis.tui", run_name="__main__", alter_sys=True)' "${TUI_ROOT}" "\$@"
EOF
)"
launcher_state=missing
if [[ -f "${TUI_LAUNCHER}" ]]; then
	launcher_state=different
	[[ "$(<"${TUI_LAUNCHER}")" == "${launcher_text}" ]] && launcher_state=current
fi

if ((CHECK)); then
	case "${tui_state}" in
	current) say "TUI al día: ${TUI_DIR}" ;;
	missing) say "TUI: falta en ${TUI_DIR} (install.sh la instala)" ;;
	different) say "TUI desactualizada: ${TUI_DIR} (install.sh la reemplaza y guarda la anterior en ${TUI_BAK})" ;;
	esac
	case "${venv_state}" in
	current) say "Entorno de la TUI al día: ${TUI_VENV}" ;;
	missing) say "Entorno de la TUI: falta en ${TUI_VENV} (install.sh lo crea con pip)" ;;
	different) say "Entorno de la TUI desactualizado: ${TUI_VENV} (install.sh lo vuelve a crear con pip)" ;;
	esac
	case "${launcher_state}" in
	current) say "Lanzador de la TUI al día: ${TUI_LAUNCHER}" ;;
	missing) say "Lanzador de la TUI: falta en ${TUI_LAUNCHER} (install.sh lo instala)" ;;
	different) say "Lanzador de la TUI desactualizado: ${TUI_LAUNCHER} (install.sh lo reemplaza y guarda el anterior en ${TUI_LAUNCHER_BAK})" ;;
	esac
	if [[ -d "${WORK_DIR}" ]]; then say "Carpeta de trabajo: ${WORK_DIR}"; else say "Carpeta de trabajo: falta ${WORK_DIR} (install.sh la crea, 0700)"; fi
	case "${input_state}" in
	current) say "Ayudante jarvis-input al día: ${INPUT_DIR}" ;;
	missing) say "Ayudante jarvis-input: falta en ${INPUT_DIR} (install.sh lo instala)" ;;
	different) say "Ayudante jarvis-input desactualizado: ${INPUT_DIR} (install.sh lo reemplaza y guarda el anterior en ${INPUT_BAK})" ;;
	esac
	case "${input_unit_state}" in
	current) say "Unidad del ayudante al día: ${INPUT_UNIT_PATH}" ;;
	missing) say "Unidad del ayudante: falta en ${INPUT_UNIT_PATH} (install.sh la instala)" ;;
	different) say "Unidad del ayudante desactualizada: ${INPUT_UNIT_PATH} (install.sh la reemplaza y guarda la anterior en ${INPUT_UNIT_BAK})" ;;
	esac
	case "${sbx_state}" in
	current) say "Envoltorio de aislamiento al día: ${SBX_PATH}" ;;
	missing) say "Envoltorio de aislamiento: falta en ${SBX_PATH} (install.sh lo instala)" ;;
	different) say "Envoltorio de aislamiento desactualizado: ${SBX_PATH} (install.sh lo reemplaza y guarda el anterior en ${SBX_BAK})" ;;
	esac
	case "${ext_state}" in
	current) say "Extensión de control de la PC al día: ${EXT_DIR}" ;;
	missing) say "Extensión de control de la PC: falta en ${EXT_DIR} (install.sh la instala)" ;;
	different) say "Extensión de control de la PC desactualizada: ${EXT_DIR} (install.sh la reemplaza y guarda la anterior en ${EXT_BAK})" ;;
	esac
	case "${stop_state}" in
	current) say "Parada al día: ${STOP_PATH}" ;;
	missing) say "Parada: falta en ${STOP_PATH} (install.sh la instala)" ;;
	different) say "Parada desactualizada: ${STOP_PATH} (install.sh la reemplaza y guarda la anterior en ${STOP_BAK})" ;;
	esac
	case "${rules_state}" in
	current) say "Reglas del agente al día: ${RULES_PATH}" ;;
	missing) say "Reglas del agente: faltan en ${RULES_PATH} (install.sh las instala)" ;;
	different) say "Reglas del agente desactualizadas: ${RULES_PATH} (install.sh las reemplaza y guarda la anterior en ${RULES_BAK})" ;;
	esac
	if ((PI_BRIDGE)); then
		if [[ -d "${PI_DATA}" ]]; then say "Datos de Pi: ${PI_DATA}"; else say "Datos de Pi: falta ${PI_DATA} (install.sh la crea, 0700)"; fi
		say "OK: puente de Pi con ${PI_BIN} y node ${node_version} (${NODE_BIN}); la unidad iría a ${SVC_PATH}"
	else
		say "OK: Gentle Dot en ${GD_DIR}, node ${node_version} (${NODE_BIN}); la unidad iría a ${UNIT_PATH}"
	fi
	exit 0
fi

# --- Install -------------------------------------------------------------------

need_systemctl
changed=1 pending=0
[[ -e "${PENDING_PATH}" ]] && pending=1
if [[ -f "${SVC_PATH}" ]] && [[ "$(<"${SVC_PATH}")" == "${unit_text}" ]]; then
	changed=0
	((pending)) || say "Sin cambios: ${SVC_PATH} ya está al día."
fi

if ((changed)); then
	if ((DRY_RUN)); then
		say "[dry-run] mkdir -p ${UNIT_DIR}"
		[[ ! -f "${SVC_PATH}" ]] || say "[dry-run] copia de la unidad anterior en ${SVC_BAK}"
		say "[dry-run] escribir ${SVC_PATH}:"
		printf '%s\n' "${unit_text}" | sed 's/^/    | /'
	else
		mkdir -p -- "${UNIT_DIR}"
		if [[ -f "${SVC_PATH}" ]]; then
			cp -f -- "${SVC_PATH}" "${SVC_BAK}"
			chmod 0644 -- "${SVC_BAK}"
			say "Copia de la unidad anterior: ${SVC_BAK}"
		fi
		# Marker first: if anything below fails, the next run still reloads.
		: >"${PENDING_PATH}"
		tmp="${SVC_PATH}.tmp.$$"
		printf '%s\n' "${unit_text}" >"${tmp}"
		chmod 0644 -- "${tmp}"
		mv -f -- "${tmp}" "${SVC_PATH}"
		say "Escrito ${SVC_PATH}"
	fi
fi

rules_changed=0
if [[ "${rules_state}" == current ]]; then
	say "Sin cambios: ${RULES_PATH} ya está al día."
else
	rules_changed=1
	if ((DRY_RUN)); then
		say "[dry-run] mkdir -p ${AGENT_HOME}"
		[[ "${rules_state}" != different ]] || say "[dry-run] copia de las reglas anteriores en ${RULES_BAK}"
		say "[dry-run] escribir ${RULES_PATH} (desde ${RULES_SRC})"
	else
		# ~/.gentle-dot also holds the token: directories this creates are private.
		(umask 077 && mkdir -p -- "${AGENT_HOME}")
		if [[ "${rules_state}" == different ]]; then
			cp -f -- "${RULES_PATH}" "${RULES_BAK}"
			chmod 0644 -- "${RULES_BAK}"
			say "Copia de las reglas anteriores: ${RULES_BAK}"
		fi
		tmp="${RULES_PATH}.tmp.$$"
		cp -f -- "${RULES_SRC}" "${tmp}"
		chmod 0644 -- "${tmp}"
		mv -f -- "${tmp}" "${RULES_PATH}"
		say "Escrito ${RULES_PATH}"
	fi
fi

ext_changed=0
if [[ "${ext_state}" == current ]]; then
	say "Sin cambios: ${EXT_DIR} ya está al día."
else
	ext_changed=1
	if ((DRY_RUN)); then
		say "[dry-run] mkdir -p ${EXT_DIR}"
		[[ "${ext_state}" != different ]] || say "[dry-run] copia de la extensión anterior en ${EXT_BAK}"
		say "[dry-run] escribir ${EXT_DIR} (desde ${EXT_SRC_DIR})"
	else
		(umask 077 && mkdir -p -- "${AGENT_HOME}/extensions")
		mkdir -p -- "${EXT_DIR}"
		chmod 0755 -- "${EXT_DIR}"
		if [[ "${ext_state}" == different ]]; then
			# Outside extensions/: a copy there would be loaded as a second extension.
			mkdir -p -- "${EXT_BAK}"
			for pair in "${EXT_FILES[@]}"; do
				[[ ! -f "${EXT_DIR}/${pair#*:}" ]] || cp -f -- "${EXT_DIR}/${pair#*:}" "${EXT_BAK}/${pair#*:}.bak"
			done
			say "Copia de la extensión anterior: ${EXT_BAK}"
		fi
		# Core first: the entry imports it, and the engine loads only index.ts.
		for ((i = ${#EXT_FILES[@]} - 1; i >= 0; i--)); do
			pair="${EXT_FILES[i]}"
			tmp="${EXT_DIR}/.${pair#*:}.tmp.$$"
			cp -f -- "${EXT_SRC_DIR}/${pair%%:*}" "${tmp}"
			chmod 0644 -- "${tmp}"
			mv -f -- "${tmp}" "${EXT_DIR}/${pair#*:}"
		done
		say "Escrito ${EXT_DIR}"
	fi
fi

if [[ "${stop_state}" == current ]]; then
	say "Sin cambios: ${STOP_PATH} ya está al día."
elif ((DRY_RUN)); then
	say "[dry-run] mkdir -p ${BIN_DIR}"
	[[ "${stop_state}" != different ]] || say "[dry-run] copia de la parada anterior en ${STOP_BAK}"
	say "[dry-run] escribir ${STOP_PATH} (desde ${STOP_SRC})"
else
	mkdir -p -- "${BIN_DIR}"
	if [[ "${stop_state}" == different ]]; then
		cp -f -- "${STOP_PATH}" "${STOP_BAK}"
		chmod 0644 -- "${STOP_BAK}"
		say "Copia de la parada anterior: ${STOP_BAK}"
	fi
	tmp="${STOP_PATH}.tmp.$$"
	cp -f -- "${STOP_SRC}" "${tmp}"
	chmod 0755 -- "${tmp}"
	mv -f -- "${tmp}" "${STOP_PATH}"
	say "Escrito ${STOP_PATH}"
fi
if [[ ":${PATH:-}:" != *":${BIN_DIR}:"* ]]; then
	say "aviso: ${BIN_DIR} no está en PATH; abre la TUI con la ruta completa: ${TUI_LAUNCHER}"
fi
# Absolute path: niri's PATH could hold an earlier, user-writable directory with a fake jarvis-stop.
say "Atajo de parada (agrégalo tú en binds.kdl de niri; este script no lo toca): Alt+Shift+Escape { spawn \"${STOP_PATH}\"; }"

# jarvis-input unit (verbatim copy; same backup and reload marker as the daemon's unit).
input_unit_changed=0
if [[ "${input_unit_state}" == current ]]; then
	((pending)) || say "Sin cambios: ${INPUT_UNIT_PATH} ya está al día."
else
	input_unit_changed=1
	if ((DRY_RUN)); then
		[[ "${input_unit_state}" != different ]] || say "[dry-run] copia de la unidad anterior en ${INPUT_UNIT_BAK}"
		say "[dry-run] escribir ${INPUT_UNIT_PATH}:"
		sed 's/^/    | /' "${INPUT_TEMPLATE}"
	else
		mkdir -p -- "${UNIT_DIR}"
		if [[ "${input_unit_state}" == different ]]; then
			cp -f -- "${INPUT_UNIT_PATH}" "${INPUT_UNIT_BAK}"
			chmod 0644 -- "${INPUT_UNIT_BAK}"
			say "Copia de la unidad anterior: ${INPUT_UNIT_BAK}"
		fi
		: >"${PENDING_PATH}"
		tmp="${INPUT_UNIT_PATH}.tmp.$$"
		cp -f -- "${INPUT_TEMPLATE}" "${tmp}"
		chmod 0644 -- "${tmp}"
		mv -f -- "${tmp}" "${INPUT_UNIT_PATH}"
		say "Escrito ${INPUT_UNIT_PATH}"
	fi
fi

# Helper package: staged next to the target, then swapped in (the previous one -> .bak).
if [[ "${input_state}" == current ]]; then
	say "Sin cambios: ${INPUT_DIR} ya está al día."
elif ((DRY_RUN)); then
	say "[dry-run] mkdir -p ${INPUT_ROOT}"
	[[ "${input_state}" != different ]] || say "[dry-run] copia del ayudante anterior en ${INPUT_BAK}"
	say "[dry-run] escribir ${INPUT_DIR} (desde ${INPUT_SRC_DIR})"
else
	mkdir -p -- "${INPUT_ROOT}"
	new="${INPUT_ROOT}/.jarvis_input.new.$$"
	mkdir -m 0755 -- "${new}"
	while IFS= read -r rel; do
		[[ -n "${rel}" ]] || continue
		mkdir -p -- "$(dirname -- "${new}/${rel}")"
		cp -f -- "${INPUT_SRC_DIR}/${rel}" "${new}/${rel}"
		chmod 0644 -- "${new}/${rel}"
	done <<<"${input_files}"
	find "${new}" -type d -exec chmod 0755 {} +
	if [[ "${input_state}" == different ]]; then
		# Only this script writes .bak; it holds the previous copy, planted files included.
		[[ ! -e "${INPUT_BAK}" ]] || rm -rf -- "${INPUT_BAK}"
		mv -f -- "${INPUT_DIR}" "${INPUT_BAK}"
		say "Copia del ayudante anterior: ${INPUT_BAK}"
	fi
	mv -- "${new}" "${INPUT_DIR}"
	say "Escrito ${INPUT_DIR}"
fi

if [[ "${sbx_state}" == current ]]; then
	say "Sin cambios: ${SBX_PATH} ya está al día."
elif ((DRY_RUN)); then
	say "[dry-run] mkdir -p ${SBX_DIR}"
	[[ "${sbx_state}" != different ]] || say "[dry-run] copia del envoltorio anterior en ${SBX_BAK}"
	say "[dry-run] escribir ${SBX_PATH} (desde ${SBX_SRC})"
else
	mkdir -p -- "${SBX_DIR}"
	if [[ "${sbx_state}" == different ]]; then
		cp -f -- "${SBX_PATH}" "${SBX_BAK}"
		chmod 0644 -- "${SBX_BAK}"
		say "Copia del envoltorio anterior: ${SBX_BAK}"
	fi
	tmp="${SBX_PATH}.tmp.$$"
	cp -f -- "${SBX_SRC}" "${tmp}"
	chmod 0644 -- "${tmp}"
	mv -f -- "${tmp}" "${SBX_PATH}"
	say "Escrito ${SBX_PATH}"
fi

# --- TUI (S15): package, venv, launcher ------------------------------------------
tui_changed=0
if [[ "${tui_state}" == current ]]; then
	say "Sin cambios: ${TUI_DIR} ya está al día."
elif ((DRY_RUN)); then
	say "[dry-run] mkdir -p ${TUI_ROOT}"
	[[ "${tui_state}" != different ]] || say "[dry-run] copia de la TUI anterior en ${TUI_BAK}"
	say "[dry-run] escribir ${TUI_DIR} (desde ${TUI_SRC_DIR})"
else
	tui_changed=1
	mkdir -p -- "${TUI_ROOT}"
	new="${TUI_ROOT}/.jarvis.new.$$"
	mkdir -m 0755 -- "${new}"
	while IFS= read -r rel; do
		[[ -n "${rel}" ]] || continue
		mkdir -p -- "$(dirname -- "${new}/${rel}")"
		cp -f -- "${TUI_SRC_DIR}/${rel}" "${new}/${rel}"
		chmod 0644 -- "${new}/${rel}"
	done <<<"${tui_files}"
	find "${new}" -type d -exec chmod 0755 {} +
	if [[ "${tui_state}" == different ]]; then
		[[ ! -e "${TUI_BAK}" ]] || rm -rf -- "${TUI_BAK}"
		mv -f -- "${TUI_DIR}" "${TUI_BAK}"
		say "Copia de la TUI anterior: ${TUI_BAK}"
	fi
	mv -- "${new}" "${TUI_DIR}"
	say "Escrito ${TUI_DIR}"
fi

# The venv is built next to the old one and swapped in only when pip succeeds: offline, the
# previous venv (if any) keeps working and the next run tries again.
if [[ "${venv_state}" == current ]]; then
	say "Sin cambios: ${TUI_VENV} ya está al día."
elif ((DRY_RUN)); then
	say "[dry-run] ${TUI_PY} -m venv ${TUI_VENV} && pip install -r ${TUI_REQ_SRC}"
else
	mkdir -p -- "${TUI_ROOT}"
	new="${TUI_ROOT}/.venv.new.$$"
	venv_ok=0
	if "${TUI_PY}" -m venv "${new}"; then
		"${new}/bin/python" -I -m pip install --disable-pip-version-check --no-input --timeout 20 --retries 1 -q \
			-r "${TUI_REQ_SRC}" && venv_ok=1
	fi
	if ((venv_ok)); then
		cp -f -- "${TUI_REQ_SRC}" "${new}/jarvis-requirements.txt"
		chmod 0644 -- "${new}/jarvis-requirements.txt"
		if [[ -d "${TUI_VENV}" ]]; then
			[[ ! -e "${TUI_VENV_BAK}" ]] || rm -rf -- "${TUI_VENV_BAK}"
			mv -f -- "${TUI_VENV}" "${TUI_VENV_BAK}"
			say "Copia del entorno anterior: ${TUI_VENV_BAK}"
		fi
		mv -- "${new}" "${TUI_VENV}"
		tmp="${TUI_ROOT}/.requirements.txt.tmp.$$"
		cp -f -- "${TUI_REQ_SRC}" "${tmp}"
		chmod 0644 -- "${tmp}"
		mv -f -- "${tmp}" "${TUI_ROOT}/requirements.txt"
		tui_changed=1
		say "Entorno de la TUI listo: ${TUI_VENV}"
	else
		# Only this script creates the staging directory; it holds a half-built venv.
		rm -rf -- "${new}"
		if [[ -d "${TUI_VENV}" ]]; then
			say "aviso: pip install falló (¿sin red?); se conserva el entorno anterior de la TUI en ${TUI_VENV}. Vuelve a ejecutar este script para reintentar."
		else
			say "aviso: pip install falló (¿sin red?); la TUI no arrancará hasta que exista ${TUI_VENV}. Vuelve a ejecutar este script con red."
		fi
	fi
fi

if [[ "${launcher_state}" == current ]]; then
	say "Sin cambios: ${TUI_LAUNCHER} ya está al día."
elif ((DRY_RUN)); then
	[[ "${launcher_state}" != different ]] || say "[dry-run] copia del lanzador anterior en ${TUI_LAUNCHER_BAK}"
	say "[dry-run] escribir ${TUI_LAUNCHER}"
else
	mkdir -p -- "${BIN_DIR}"
	if [[ "${launcher_state}" == different ]]; then
		# Not executable: a backup must not be run by accident (e.g. the old checkout launcher).
		cp -f -- "${TUI_LAUNCHER}" "${TUI_LAUNCHER_BAK}"
		chmod 0644 -- "${TUI_LAUNCHER_BAK}"
		say "Copia del lanzador anterior: ${TUI_LAUNCHER_BAK}"
	fi
	tmp="${TUI_LAUNCHER}.tmp.$$"
	printf '%s\n' "${launcher_text}" >"${tmp}"
	chmod 0755 -- "${tmp}"
	mv -f -- "${tmp}" "${TUI_LAUNCHER}"
	tui_changed=1
	say "Escrito ${TUI_LAUNCHER}"
fi
# The TUI is not a service: a running one keeps the old code until it is reopened.
((tui_changed)) && say "${TUI_HINT}"

# Every ReadOnlyPaths= and ReadWritePaths= entry of the daemon's unit must exist before it
# starts: the unit lists them without "-", so a missing one fails the start instead of
# being skipped. All others were just installed. The desktop entries directory is created
# too (the helper reads it; under the read-only home the daemon cannot plant one there).
if [[ ! -d "${APPS_DIR}" ]]; then
	if ((DRY_RUN)); then say "[dry-run] mkdir -p ${APPS_DIR}"; else mkdir -p -- "${APPS_DIR}"; fi
fi
# ~/.gentle-dot also holds the token: private, like the agent home above.
if [[ ! -d "${GD_DATA}" ]]; then
	if ((DRY_RUN)); then say "[dry-run] mkdir -m 0700 ${GD_DATA}"; else mkdir -m 0700 -- "${GD_DATA}"; fi
fi
# The user's work folder: the only place in the home, besides its data, the agent can write.
if [[ ! -d "${WORK_DIR}" ]]; then
	if ((DRY_RUN)); then
		say "[dry-run] mkdir -m 0700 ${WORK_DIR}"
	else
		mkdir -m 0700 -- "${WORK_DIR}"
		say "Carpeta de trabajo del agente: ${WORK_DIR}"
	fi
fi
# S10: Pi's data folder, writable for the bridge (ReadWritePaths=, no "-"); private like ~/.gentle-dot.
if ((PI_BRIDGE)) && [[ ! -d "${PI_DATA}" ]]; then
	if ((DRY_RUN)); then say "[dry-run] mkdir -m 0700 ${PI_DATA}"; else mkdir -m 0700 -- "${PI_DATA}"; fi
fi
input_changed=0
[[ "${input_state}" == current && "${input_unit_state}" == current ]] || input_changed=1

# A pending reload counts as a change: systemd may still hold the previous unit.
if ((changed || input_unit_changed || pending)); then
	((pending)) && say "${PENDING_MSG}"
	run systemctl --user daemon-reload ||
		die "systemctl --user daemon-reload falló; ${SVC_PATH} quedó escrita pero systemd no la cargó. Vuelve a ejecutar este script para reintentar la recarga."
	((DRY_RUN)) || rm -f -- "${PENDING_PATH}"
	# A pending reload may be owed for either unit: restart both.
	((pending)) && changed=1 input_changed=1
fi

if ((ENABLE)); then
	# The helper first: the daemon's agent talks to it.
	if ((HEADLESS)); then
		say "Sin sesión gráfica (--headless): ${INPUT_UNIT_NAME} queda instalado pero no se habilita."
	else
		((input_changed)) && run systemctl --user try-restart "${INPUT_UNIT_NAME}"
		run systemctl --user enable --now "${INPUT_UNIT_NAME}"
	fi
	# S10: Gentle Dot and the bridge listen on the same port. The other one, when installed,
	# is stopped and disabled first (its unit file stays, so switching back is one more run);
	# Conflicts= in the bridge's unit also stops it if something starts it later.
	if [[ -e "${ALT_PATH}" ]]; then
		run systemctl --user disable --now "${ALT_NAME}"
	fi
	# A running service picks up a changed unit, agent rules or extension (read when the
	# engine starts); then enable at login and start it.
	((changed || rules_changed || ext_changed)) && run systemctl --user try-restart "${SVC_NAME}"
	if ((stale)); then
		# reenable = disable (removes every link to the unit) + enable (the current [Install]).
		say "La unidad seguía habilitada en ${OTHER_TARGET}; se vuelve a habilitar en ${WANTED_TARGET}."
		run systemctl --user reenable "${SVC_NAME}"
		run systemctl --user start "${SVC_NAME}"
	else
		run systemctl --user enable --now "${SVC_NAME}"
	fi
else
	((stale)) && say "aviso: la unidad sigue habilitada en ${OTHER_TARGET}; para pasarla a ${WANTED_TARGET}: systemctl --user reenable ${SVC_NAME} (o este script con --enable)."
	((ext_changed)) && say "La extensión de control de la PC se carga al arrancar el motor: si el servicio ya corre, reinícialo para aplicarla: systemctl --user try-restart ${SVC_NAME} (o este script con --enable)."
	((rules_changed)) && say "Las reglas del agente se leen al arrancar el motor: si el servicio ya corre, reinícialo para aplicarlas: systemctl --user try-restart ${SVC_NAME} (o este script con --enable)."
	((changed)) && say "El aislamiento de ${SVC_NAME} se aplica al arrancar el daemon: si ya corre, reinícialo: systemctl --user try-restart ${SVC_NAME} (o este script con --enable)."
	[[ ! -e "${ALT_PATH}" ]] || say "aviso: ${ALT_NAME} sigue instalado y usa el mismo puerto; para dejar solo ${SVC_NAME}: systemctl --user disable --now ${ALT_NAME} (o este script con --enable)."
	((input_changed && !HEADLESS)) && say "El ayudante jarvis-input cambió: si ya corre, reinícialo para aplicarlo: systemctl --user try-restart ${INPUT_UNIT_NAME} (o este script con --enable)."
	say "No se habilitó ni arrancó. Para hacerlo: systemctl --user enable --now ${INPUT_UNIT_NAME} ${SVC_NAME} (o este script con --enable)."
fi
if ((HEADLESS)); then
	say "Para que arranque sin iniciar sesión (opcional, no lo hace este script): loginctl enable-linger \"\$USER\""
else
	say "Arranca con la sesión gráfica (graphical-session.target) y se detiene al cerrarla."
fi
