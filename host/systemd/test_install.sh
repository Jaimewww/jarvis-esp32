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
		bash "${INSTALL}" "$@" 2>&1)"
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
	'grep -qF "ExecStart=\"${STUBS}/node\" \"${GD}/packages/daemon/src/cli.ts\"" <<<"${OUT}"'
check "dry-run install shows daemon-reload" 'grep -qF "[dry-run] systemctl --user daemon-reload" <<<"${OUT}"'
check "dry-run install writes nothing" '[[ ! -e "${FAKE_CONFIG}" ]]'
check "dry-run install calls no systemctl" '[[ ! -s "${CALLS}" ]]'

# 2. --check: verifies only.
sandboxed "${BASE[@]}" --check
check "--check exits 0 and writes nothing" '[[ ${RC} -eq 0 && ! -e "${FAKE_CONFIG}" && ! -s "${CALLS}" ]]'

# 3. Install: writes the rendered unit and reloads; does not enable.
sandboxed "${BASE[@]}"
check "install exits 0" '[[ ${RC} -eq 0 ]]'
check "install writes the unit" '[[ -f "${UNIT}" ]]'
check "unit has no placeholders left" '! grep -q "@[A-Z_]*@" "${UNIT}"'
check "unit points at the checkout and node" \
	'grep -qxF "ExecStart=\"${STUBS}/node\" \"${GD}/packages/daemon/src/cli.ts\"" "${UNIT}"'
check "install only reloads" '[[ "$(calls)" == "--user daemon-reload" ]]'
check "unit is 0644" '[[ "$(stat -c %a "${UNIT}")" == 644 ]]'
check "nothing outside the user unit dir" \
	'[[ "$(cd "${FAKE_CONFIG}" && find . -mindepth 1 | sort | tr "\n" " ")" == "./systemd ./systemd/user ./systemd/user/gentle-dot.service " && -z "$(ls -A "${FAKE_HOME}")" ]]'

# 4. Idempotency: a second install changes nothing and calls nothing.
before="$(sha256sum "${UNIT}")"
reset_calls
sandboxed "${BASE[@]}"
check "second install exits 0" '[[ ${RC} -eq 0 ]]'
check "second install reports no changes" 'grep -q "Sin cambios" <<<"${OUT}"'
check "second install leaves the unit" '[[ "$(sha256sum "${UNIT}")" == "${before}" ]]'
check "second install calls no systemctl" '[[ ! -s "${CALLS}" ]]'
check "an unchanged install keeps no backup" '[[ ! -e "${UNIT}.bak" ]]'

# 5. --enable on an unchanged unit: only enable --now.
reset_calls
sandboxed "${BASE[@]}" --enable
check "--enable exits 0" '[[ ${RC} -eq 0 ]]'
check "--enable runs enable --now only" '[[ "$(calls)" == "--user enable --now gentle-dot.service" ]]'

# 5b. --enable on a stale unit: rewrite, reload, restart if running, enable --now.
printf '# stale\n' >>"${UNIT}"
reset_calls
sandboxed "${BASE[@]}" --enable
check "--enable on a stale unit rewrites it" '[[ ${RC} -eq 0 && "$(sha256sum "${UNIT}")" == "${before}" ]]'
check "--enable on a stale unit reloads, try-restarts, enables" \
	'[[ "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart gentle-dot.service\n--user enable --now gentle-dot.service'"'"' ]]'

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
check "upgrade from default.target rewrites the unit" '[[ ${RC} -eq 0 && "$(sha256sum <"${UNIT_UP}")" == "$(sha256sum <"${UNIT}")" ]]'
check "upgrade from default.target reloads, try-restarts, reenables, starts" \
	'[[ "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart gentle-dot.service\n--user reenable gentle-dot.service\n--user start gentle-dot.service'"'"' ]] && grep -qxF "${reenable_msg}" <<<"${OUT}"'
reset_calls
CFG="${CFG_UP}" sandboxed "${BASE[@]}" --enable
check "--enable on an unchanged unit with a stale link only reenables and starts" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user reenable gentle-dot.service\n--user start gentle-dot.service'"'"' ]]'
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
check "--headless --enable over a graphical-session link reenables and starts" \
	'[[ ${RC} -eq 0 && "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart gentle-dot.service\n--user reenable gentle-dot.service\n--user start gentle-dot.service'"'"' ]]'
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
	'[[ ${RC} -eq 0 ]] && grep -qxF "Recarga pendiente: un daemon-reload anterior falló; se reintenta." <<<"${OUT}" && ! grep -q "Sin cambios" <<<"${OUT}"'
check "rerun after a failed reload reloads, try-restarts, enables" \
	'[[ "$(calls)" == $'"'"'--user daemon-reload\n--user try-restart gentle-dot.service\n--user enable --now gentle-dot.service'"'"' ]]'
check "successful retry clears the marker" '[[ ! -e "${PENDING}" ]]'
reset_calls
sandboxed "${BASE[@]}"
check "after the retry the next run has no changes" \
	'[[ ${RC} -eq 0 && ! -s "${CALLS}" ]] && grep -qF "Sin cambios" <<<"${OUT}"'

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

# 6. Dry-run uninstall: prints, removes nothing, calls nothing.
reset_calls
sandboxed --uninstall --dry-run
check "dry-run uninstall exits 0" '[[ ${RC} -eq 0 ]]'
check "dry-run uninstall shows disable, rm, reload" \
	'grep -qF "[dry-run] systemctl --user disable --now gentle-dot.service" <<<"${OUT}" &&
	 grep -qF "[dry-run] rm -f -- ${UNIT}" <<<"${OUT}" &&
	 grep -qF "[dry-run] systemctl --user daemon-reload" <<<"${OUT}"'
check "dry-run uninstall keeps the unit" '[[ -f "${UNIT}" && ! -s "${CALLS}" ]]'

# 7. Uninstall, then again (idempotent).
sandboxed --uninstall
check "uninstall exits 0" '[[ ${RC} -eq 0 ]]'
check "uninstall removes the unit" '[[ ! -e "${UNIT}" ]]'
check "uninstall disables, reloads, resets" \
	'[[ "$(calls)" == $'"'"'--user disable --now gentle-dot.service\n--user daemon-reload\n--user reset-failed gentle-dot.service'"'"' ]]'
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
	 [[ "$(calls)" == $'"'"'--user daemon-reload\n--user reset-failed gentle-dot.service'"'"' && ! -e "${PENDING}" ]]'
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
else
	printf 'skip systemd-analyze not installed\n'
fi

printf '\n%d passed, %d failed\n' "${PASS}" "${FAIL}"
((FAIL == 0))
