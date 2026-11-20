"""`open` (S15): resolve a desktop entry and refuse anything the sandboxed daemon could rewrite.

The helper does not hand the id to `gtk-launch` (GIO would re-resolve it with its own precedence,
and may D-Bus-activate the app through service files under $HOME) nor the URL to a generic
`xdg-open`. It finds the entry itself, checks it, parses `Exec`, and the server execs exactly the
argv that was checked. Reads files only; never executes anything.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import checks

# The only PATH used to find `Exec` programs and given to launched apps; never the inherited one.
LAUNCH_PATH = "/usr/local/bin:/usr/bin"
# Besides $HOME (and $XDG_RUNTIME_DIR, added by the server): places any local process can write.
SYSTEM_UNTRUSTED: tuple[Path, ...] = (Path("/tmp"), Path("/var/tmp"), Path("/dev/shm"))
DROP_ENV = ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT")

SHELLS = {"sh", "bash", "dash", "zsh", "fish", "ksh", "mksh", "ash", "csh", "tcsh", "busybox", "nu",
          "elvish", "xonsh", "rbash"}
_INTERPRETER = re.compile(r"(python|perl|ruby|node|nodejs|deno|bun|lua|luajit|php|tclsh|wish|"
                          r"rscript|pwsh|osascript|guile|racket|julia)[0-9.]*")
_INLINE_FLAGS = {"-c", "-e", "-E", "-m", "-r", "--eval", "--command", "--exec", "--print", "-p"}
_FIELD = re.compile(r"%(.)")
_URL_CODES = {"u", "U", "f", "F"}


class Refused(ValueError):
    """`open` is refused; the message goes back to the agent."""


@dataclass(frozen=True)
class Launch:
    app_id: str
    desktop: Path  # the .desktop file that was checked
    argv: list[str]  # argv[0] as written in Exec; the program actually run is `executable`
    executable: str
    cwd: str


# --- Exec parsing (Desktop Entry Specification, "The Exec key") -----------------------------


def parse_exec(value: str) -> list[str]:
    """Split an already string-unescaped Exec value into arguments. ValueError on bad quoting.

    Whitespace separates arguments; inside double quotes a backslash escapes the next character
    (`\\"`, `\\$`, `` \\` ``, `\\\\`). Field codes are kept (see `expand_field_codes`).
    """
    args: list[str] = []
    cur: list[str] = []
    in_arg = quoted = False
    i = 0
    while i < len(value):
        c = value[i]
        if quoted:
            if c == "\\" and i + 1 < len(value):
                i += 1
                cur.append(value[i])
            elif c == '"':
                quoted = False
            else:
                cur.append(c)
        elif c == '"':
            quoted = in_arg = True
        elif c in " \t\n":
            if in_arg:
                args.append("".join(cur))
                cur, in_arg = [], False
        elif c == "\\" and i + 1 < len(value):
            i += 1
            cur.append(value[i])
            in_arg = True
        else:
            cur.append(c)
            in_arg = True
        i += 1
    if quoted:
        raise ValueError("comillas sin cerrar")
    if in_arg:
        args.append("".join(cur))
    if not args or not args[0]:
        raise ValueError("vacío")
    return args


def expand_field_codes(args: list[str], url: str | None) -> list[str]:
    """Drop field codes (`%i %c %k %d …`, and `%f %F %u %U` when there is no URL); `%%` -> `%`.

    With a URL, the first standalone `%u/%U/%f/%F` argument becomes the URL (the rest are dropped);
    when there is none, the URL is appended.
    """
    out: list[str] = []
    placed = False
    for arg in args:
        if len(arg) == 2 and arg[0] == "%" and arg[1] != "%":
            if url is not None and arg[1] in _URL_CODES and not placed:
                out.append(url)
                placed = True
            continue
        out.append(_FIELD.sub(lambda m: "%" if m[1] == "%" else "", arg))
    if url is not None and not placed:
        out.append(url)
    return out


# --- environment ---------------------------------------------------------------------------


def launch_env(env: Mapping[str, str]) -> dict[str, str]:
    """The helper's environment for a launched app: fixed PATH, no LD_PRELOAD & co."""
    out = {k: v for k, v in env.items() if k not in DROP_ENV}
    out["PATH"] = LAUNCH_PATH
    return out


def parse_default_handler(stdout: str) -> str | None:
    """`xdg-mime query default x-scheme-handler/https` -> desktop-id, or None."""
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    if len(lines) != 1 or not lines[0].endswith(".desktop"):
        return None
    app_id = lines[0][: -len(".desktop")]
    return None if checks.validate_desktop_id(app_id) else app_id


# --- checks --------------------------------------------------------------------------------


def is_writable(path: Path) -> bool:
    """Whether this user could replace `path`: the file itself or its directory is writable."""
    return os.access(path, os.W_OK, follow_symlinks=True) or os.access(path.parent, os.W_OK)


def _roots(home: Path, extra: Iterable[Path]) -> list[str]:
    roots: list[str] = []
    for r in (home, *extra):
        for form in (os.path.normpath(str(r)), os.path.realpath(r)):
            if form != "/" and form not in roots:
                roots.append(form)
    return roots


def _under(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r + "/") for r in roots)


def _mentions(arg: str, roots: list[str]) -> bool:
    return arg.startswith("~") or any(arg.endswith(r) or (r + "/") in arg for r in roots)


def _where(roots: list[str]) -> str:
    return "$HOME" if len(roots) <= 2 else "$HOME o una carpeta temporal"


def find_entry(app_id: str, data_dirs: Iterable[Path]) -> Path | None:
    """The .desktop file for `app_id` by XDG precedence (the same walk as `apps`), or None."""
    for d in data_dirs:
        apps_dir = d / "applications"
        if not apps_dir.is_dir():
            continue
        for found_id, path in checks._iter_desktop_files(apps_dir):
            if found_id == app_id:
                return path
    return None


def _program(name: str, search_path: str) -> Path:
    if name.startswith("~"):
        raise Refused(f"Exec empieza con «{name[:80]}», que está en $HOME.")
    if "/" in name:
        if not os.path.isabs(name):
            raise Refused(f"Exec usa «{name[:80]}»: el programa debe ser un nombre o una ruta absoluta.")
        p = Path(name)
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise Refused(f"El programa «{name[:80]}» no existe o no es ejecutable.")
    for d in search_path.split(":"):
        if d and os.path.isabs(d):
            p = Path(d) / name
            if p.is_file() and os.access(p, os.X_OK):
                return p
    raise Refused(f"El programa «{name[:80]}» no está en {search_path}.")


def _base_name(arg: str) -> str:
    return os.path.basename(arg).casefold()


_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)


def _inline_code(args: list[str]) -> bool:
    """A shell or interpreter anywhere in argv followed by an inline-code flag, or `env -S`.

    Strict on purpose: for a shell/interpreter every later argument counts (a script that takes
    its own `-c` is refused too). For `env`, only its own options (before the wrapped program).
    """
    for i, a in enumerate(args):
        b = _base_name(a)
        if b == "env":
            skip = False
            for later in args[i + 1:]:
                if skip:
                    skip = False
                    continue
                if later == "--" or not (later.startswith("-") or _ASSIGNMENT.fullmatch(later)):
                    break
                if later.startswith("--split-string") or re.fullmatch(r"-[A-Za-z]*S.*", later, re.DOTALL):
                    return True
                skip = later in ("-u", "-C", "--unset", "--chdir")
            continue
        shell = b in SHELLS
        if not (shell or _INTERPRETER.fullmatch(b)):
            continue
        for later in args[i + 1:]:
            if later in _INLINE_FLAGS or later.startswith(("--eval=", "--command=")):
                return True
            if shell and re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", later):
                return True
    return False


def resolve(app_id: str, data_dirs: Iterable[Path], *, home: Path, search_path: str | None = None,
            url: str | None = None, extra_untrusted: Iterable[Path] | None = None,
            is_writable: Callable[[Path], bool] | None = None) -> Launch:
    """Check `app_id` (and the URL to hand it) and return what to exec. Refused otherwise."""
    search_path = LAUNCH_PATH if search_path is None else search_path
    extra = SYSTEM_UNTRUSTED if extra_untrusted is None else tuple(extra_untrusted)
    writable = globals()["is_writable"] if is_writable is None else is_writable
    roots = _roots(home, extra)
    where = _where(roots)

    if checks.validate_desktop_id(app_id):
        raise Refused(checks.validate_desktop_id(app_id) or "")
    if checks._is_terminal(app_id.casefold()):
        raise Refused(f"«{app_id}» es una terminal; abrirla daría órdenes fuera del agente.")
    path = find_entry(app_id, data_dirs)
    if path is None:
        raise Refused(f"No encontré la app «{app_id}» (usa la lista de apps).")
    real = Path(os.path.realpath(path))
    if _under(os.path.normpath(str(path)), roots) or _under(str(real), roots):
        raise Refused(f"La entrada de «{app_id}» ({path}) está en {where}, que el agente podría "
                      "modificar; solo se abren apps instaladas en el sistema.")
    if writable(real):
        raise Refused(f"La entrada de «{app_id}» ({path}) se puede modificar sin ser root; no se abre.")
    keys = checks._read_entry(path)
    if not keys or keys.get("Type") != "Application":
        raise Refused(f"«{app_id}» no es una aplicación (Type=Application).")
    if keys.get("Hidden", "").lower() == "true" or keys.get("NoDisplay", "").lower() == "true":
        raise Refused(f"«{app_id}» es una entrada oculta (Hidden/NoDisplay); no se abre.")
    if keys.get("Terminal", "").lower() == "true":
        raise Refused(f"«{app_id}» corre en una terminal (Terminal=true); no se abre.")
    if checks._is_terminal(keys.get("StartupWMClass", "").casefold()):
        raise Refused(f"«{app_id}» es una terminal; abrirla daría órdenes fuera del agente.")
    exec_ = keys.get("Exec", "")
    try:
        args = expand_field_codes(parse_exec(exec_), None)
    except ValueError:
        raise Refused(f"«{app_id}» no tiene un Exec válido.") from None
    if not args or not args[0]:
        raise Refused(f"«{app_id}» no tiene un Exec válido.")

    for a in args:
        if _mentions(a, roots):
            raise Refused(f"El Exec de «{app_id}» usa «{a[:120]}», que está en {where}; no se abre.")
    if _inline_code(args):
        raise Refused(f"El Exec de «{app_id}» corre código en línea (shell o intérprete con -c/-e/-m o "
                      "env -S); no se abre.")
    program = _program(args[0], search_path)
    program_real = os.path.realpath(program)
    if _under(os.path.normpath(str(program)), roots) or _under(program_real, roots):
        raise Refused(f"El programa de «{app_id}» ({program_real}) está en {where}; no se abre.")
    if writable(Path(program_real)):
        raise Refused(f"El programa de «{app_id}» ({program_real}) se puede modificar sin ser root; no se abre.")
    for a in [args[0], program_real, *[x for x in args[1:] if not x.startswith("-")]]:
        if checks._is_terminal(_base_name(a)):
            raise Refused(f"«{app_id}» abre una terminal ({_base_name(a)}); no se abre.")

    cwd = "/"
    work = keys.get("Path", "")
    if work:
        if not os.path.isabs(work) or _under(os.path.normpath(work), roots) or \
                _under(os.path.realpath(work), roots):
            raise Refused(f"«{app_id}» arranca en {work[:120]} ({where}); no se abre.")
        if os.path.isdir(work):
            cwd = work

    argv = expand_field_codes(parse_exec(exec_), url)
    return Launch(app_id=app_id, desktop=path, argv=argv, executable=str(program), cwd=cwd)
