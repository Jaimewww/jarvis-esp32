#!/usr/bin/env bash
# Install, check or remove the Gentle Dot daemon as a systemd *user* service.
#
#   host/systemd/install.sh --gentle-dot-dir DIR [--node PATH] [--dry-run] [--enable]
#   host/systemd/install.sh --gentle-dot-dir DIR [--node PATH] --check
#   host/systemd/install.sh --uninstall [--dry-run]
#
# It only writes ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gentle-dot.service and
# only calls `systemctl --user`. It never runs as root, never enables linger, never
# installs, builds or downloads Gentle Dot, and never touches ~/.gentle-dot.
# See host/README.md, section "Daemon de Gentle Dot como servicio".
set -euo pipefail

readonly UNIT_NAME="gentle-dot.service"
# Upstream commit the decisions pin (docs/decisiones.md D1 and D8).
readonly PINNED_COMMIT="f97d56d884b1d3c178f415b95d486464e7b220bf"
readonly NODE_MIN_MAJOR=24
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly TEMPLATE="${SCRIPT_DIR}/${UNIT_NAME}.in"

usage() {
	cat <<'EOF'
Uso: install.sh [opciones]

  --gentle-dot-dir DIR  checkout de Gentle Dot (también por GENTLE_DOT_DIR)
  --node PATH           binario de Node 24+ (por defecto: `command -v node`)
  --enable              además habilita y arranca el servicio (enable --now)
  --check               solo verifica los requisitos; no escribe ni llama a systemctl
  --dry-run             imprime lo que haría, sin escribir ni llamar a systemctl
  --uninstall           detiene, deshabilita y borra la unidad
  --allow-unpinned      acepta un checkout en otro commit (desaconsejado)
  -h, --help            esta ayuda
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

DRY_RUN=0 ENABLE=0 CHECK=0 UNINSTALL=0 ALLOW_UNPINNED=0
GD_DIR="${GENTLE_DOT_DIR:-}" NODE_BIN=""

while (($#)); do
	case "$1" in
	--gentle-dot-dir) [[ $# -ge 2 ]] || die "--gentle-dot-dir necesita un valor"; GD_DIR="$2"; shift 2 ;;
	--node) [[ $# -ge 2 ]] || die "--node necesita un valor"; NODE_BIN="$2"; shift 2 ;;
	--enable) ENABLE=1; shift ;;
	--check) CHECK=1; shift ;;
	--dry-run) DRY_RUN=1; shift ;;
	--uninstall) UNINSTALL=1; shift ;;
	--allow-unpinned) ALLOW_UNPINNED=1; shift ;;
	-h | --help) usage; exit 0 ;;
	*) usage >&2; die "opción desconocida: $1" ;;
	esac
done

((UNINSTALL && (ENABLE || CHECK))) && die "--uninstall no se combina con --enable ni --check"

# --- Who and where -------------------------------------------------------------

[[ "$(id -u)" != "0" ]] || die "no se ejecuta como root: es un servicio de usuario (systemctl --user)"
[[ -n "${HOME:-}" && "${HOME}" == /* ]] || die "HOME no está definido o no es una ruta absoluta"
CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
[[ "${CONFIG_HOME}" == /* ]] || die "XDG_CONFIG_HOME debe ser una ruta absoluta"
readonly UNIT_DIR="${CONFIG_HOME}/systemd/user"
readonly UNIT_PATH="${UNIT_DIR}/${UNIT_NAME}"

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

if ((UNINSTALL)); then
	if [[ ! -e "${UNIT_PATH}" ]]; then
		say "No hay unidad en ${UNIT_PATH}; nada que desinstalar."
		exit 0
	fi
	need_systemctl
	# disable --now fails when the manager no longer knows the unit; removal goes on.
	run systemctl --user disable --now "${UNIT_NAME}" || say "aviso: disable --now falló; se sigue"
	run rm -f -- "${UNIT_PATH}"
	run systemctl --user daemon-reload
	run systemctl --user reset-failed "${UNIT_NAME}" 2>/dev/null || true
	say "Desinstalado. Los datos del daemon (~/.gentle-dot, con su token) no se tocaron."
	exit 0
fi

# --- Prerequisites -------------------------------------------------------------

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

[[ -f "${TEMPLATE}" ]] || die "falta la plantilla ${TEMPLATE}"
unit_text="$(sed -e "s|@NODE@|${NODE_BIN}|g" -e "s|@GENTLE_DOT_DIR@|${GD_DIR}|g" "${TEMPLATE}")"
! grep -q '@[A-Z_]*@' <<<"${unit_text}" || die "la plantilla dejó variables sin reemplazar"

if ((CHECK)); then
	say "OK: Gentle Dot en ${GD_DIR}, node ${node_version} (${NODE_BIN}); la unidad iría a ${UNIT_PATH}"
	exit 0
fi

# --- Install -------------------------------------------------------------------

need_systemctl
changed=1
if [[ -f "${UNIT_PATH}" ]] && [[ "$(<"${UNIT_PATH}")" == "${unit_text}" ]]; then
	changed=0
	say "Sin cambios: ${UNIT_PATH} ya está al día."
fi

if ((changed)); then
	if ((DRY_RUN)); then
		say "[dry-run] mkdir -p ${UNIT_DIR}"
		say "[dry-run] escribir ${UNIT_PATH}:"
		printf '%s\n' "${unit_text}" | sed 's/^/    | /'
	else
		mkdir -p -- "${UNIT_DIR}"
		tmp="${UNIT_PATH}.tmp.$$"
		printf '%s\n' "${unit_text}" >"${tmp}"
		chmod 0644 -- "${tmp}"
		mv -f -- "${tmp}" "${UNIT_PATH}"
		say "Escrito ${UNIT_PATH}"
	fi
	run systemctl --user daemon-reload
fi

if ((ENABLE)); then
	# A running service picks up a changed unit; then enable at login and start it.
	((changed)) && run systemctl --user try-restart "${UNIT_NAME}"
	run systemctl --user enable --now "${UNIT_NAME}"
else
	say "No se habilitó ni arrancó. Para hacerlo: systemctl --user enable --now ${UNIT_NAME} (o este script con --enable)."
fi
say "Para que arranque sin iniciar sesión (opcional, no lo hace este script): loginctl enable-linger \"\$USER\""
