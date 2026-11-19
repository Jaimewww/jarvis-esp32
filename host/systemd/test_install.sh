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

# systemctl stub: records its arguments, never touches a manager.
cat >"${STUBS}/systemctl" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"${CALLS}"
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
	OUT="$(env -i PATH="${STUBS}:/usr/bin:/bin" HOME="${FAKE_HOME}" XDG_CONFIG_HOME="${FAKE_CONFIG}" \
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
else
	printf 'skip systemd-analyze not installed\n'
fi

printf '\n%d passed, %d failed\n' "${PASS}" "${FAIL}"
((FAIL == 0))
