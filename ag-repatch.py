#!/usr/bin/env python3
"""ag-repatch — re-applies Antigravity's region patch after it updates itself.

Two same-length renames inside the language server / `agy` binary lift the
region gate:

    ineligible  -> inexigible     the protobuf descriptor field; this IS the patch
    https_proxy -> AG_LS_PROXY    the proxy variable name; enables the proxy route

Same length means offsets, relocations and the executable layout are untouched,
so the edit is byte-exact and reversible, and applying it is a handful of
seek-writes rather than a 200 MB rewrite that bricks the install if interrupted.

Antigravity updates itself and restores the stock bytes, so this is run again
after each update. Linux, Windows and macOS; Python 3.8+, standard library only.

    ag-repatch              patch everything found
    ag-repatch --check      report state, change nothing
    ag-repatch --selftest   run the internal checks

Exit: 0 fine, 1 something failed, 2 nothing found, 3 selftest failed.
"""



# --------------------------------------------------------------------------
# Part 1 — patch engine. OS-agnostic, stdlib only, returns codes and never prose.
# --------------------------------------------------------------------------


import dataclasses
import errno
import os
import pathlib
import sys
from typing import List, Optional, Sequence, Tuple

PAIRS = [
    (b"ineligible", b"inexigible"),      # protobuf descriptor field; this IS the patch
    (b"https_proxy", b"AG_LS_PROXY"),    # proxy env var name; additive, enables the proxy route
]  # type: List[Tuple[bytes, bytes]]

PROXY_URL = "http://127.0.0.1:53129"  # type: str

_ETXTBSY = getattr(errno, "ETXTBSY", None)

# The two places an install keeps a language server, relative to its root,
# plus the binaries Windows drops in the root itself.
_GLOBS = (
    "resources/bin/language_server*",
    "resources/app/extensions/antigravity/bin/language_server*",
    "agy",
    "agy.exe",
    "language_server*.exe",
)

# Read-only squashfs: patching there can only ever fail.
_SKIP_PREFIXES = ("/snap", "/var/lib/snapd")


@dataclasses.dataclass(frozen=True)
class Target:
    """One binary worth patching."""

    path: pathlib.Path      # resolved, deduped
    kind: str               # "cli" | "language-server"
    product: str            # "Antigravity CLI" | "Antigravity IDE" | "Antigravity" | "unknown"


@dataclasses.dataclass(frozen=True)
class Result:
    """Outcome of one patch attempt. `detail` is raw OS text, never a sentence."""

    target: Target
    code: str               # "patched"|"already"|"no-signature"|"locked"|"denied"|"error"
    replaced: int           # byte ranges rewritten this run; 0 for every code but "patched"
    detail: str             # raw OS error text or ""


# --------------------------------------------------------------------------- scan


def _find_all(data: bytes, needle: bytes) -> List[int]:
    """Every offset of `needle`, non-overlapping, in order."""
    out = []  # type: List[int]
    i = data.find(needle)
    while i != -1:
        out.append(i)
        i = data.find(needle, i + len(needle))
    return out


def _scan(data: bytes) -> Optional[List[Tuple[List[int], int]]]:
    """Per pair: (offsets still stock, count already patched).

    None when any pair is missing both spellings — an unrecognised build, which
    must be left exactly as the updater left it even if the *other* pair matched.
    """
    out = []  # type: List[Tuple[List[int], int]]
    for old, new in PAIRS:
        stock = _find_all(data, old)
        patched = len(_find_all(data, new))
        if not stock and not patched:
            return None
        out.append((stock, patched))
    return out


# --------------------------------------------------------------------------- discovery


def _classify(path: pathlib.Path) -> Tuple[str, str]:
    """(kind, product) from the path shape alone — no file is opened."""
    name = path.name.lower()
    kind = "cli" if name.startswith("agy") else "language-server"
    low = str(path).lower().replace("\\", "/")
    if "antigravity-ide" in low or "antigravity ide" in low:
        product = "Antigravity IDE"
    elif "antigravity" in low:
        product = "Antigravity"
    elif kind == "cli":
        product = "Antigravity CLI"
    else:
        product = "unknown"
    return kind, product


def _skipped(path: pathlib.Path) -> bool:
    """True for snap mounts: read-only squashfs, a write there can only fail."""
    low = str(path).replace("\\", "/")
    return any(low == p or low.startswith(p + "/") for p in _SKIP_PREFIXES)


def _expand(root: pathlib.Path) -> List[pathlib.Path]:
    """Every candidate under one install root."""
    out = []  # type: List[pathlib.Path]
    for pattern in _GLOBS:
        try:
            out.extend(sorted(root.glob(pattern)))
        except OSError:
            pass
    return out


def _roots_linux(home: pathlib.Path) -> Tuple[List[pathlib.Path], List[pathlib.Path]]:
    files = [
        home / ".local/bin/agy",
        home / ".agy/bin/agy",
        home / ".local/share/agy/bin/agy",
    ]
    roots = [pathlib.Path("/opt/antigravity-ide"), pathlib.Path("/opt/antigravity")]
    # Case variants without a case-insensitive glob: spell the first letter out.
    for base in (pathlib.Path("/usr/share"), home / ".local/share"):
        try:
            roots.extend(sorted(base.glob("[Aa]ntigravity*")))
        except OSError:
            pass
    return files, roots


def _roots_macos(home: pathlib.Path) -> Tuple[List[pathlib.Path], List[pathlib.Path]]:
    files = [
        home / ".local/bin/agy",
        pathlib.Path("/usr/local/bin/agy"),
        pathlib.Path("/opt/homebrew/bin/agy"),
    ]
    roots = []  # type: List[pathlib.Path]
    for apps in (pathlib.Path("/Applications"), home / "Applications"):
        try:
            # Names vary (_macos_arm64 / _x64 and the bundle's own casing), so glob.
            roots.extend(sorted(apps.glob("[Aa]ntigravity*.app/Contents/Resources")))
        except OSError:
            pass
    return files, roots


def _roots_windows(home: pathlib.Path) -> Tuple[List[pathlib.Path], List[pathlib.Path]]:
    roots = []  # type: List[pathlib.Path]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = pathlib.Path(local)
        roots += [
            base / "Programs/Antigravity",
            base / "Programs/Antigravity IDE",
            base / "agy",
            base / "agy/bin",
        ]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        val = os.environ.get(var)
        if val:
            roots += [pathlib.Path(val) / "Antigravity", pathlib.Path(val) / "Antigravity IDE"]
    return [], roots


def find_targets(extra: Sequence[pathlib.Path] = ()) -> List[Target]:
    """Every existing regular file worth patching, deduped by resolved path.

    A PATH `agy` is usually a symlink at the real binary, so dedup happens after
    resolve() or the same file gets patched twice and counted twice. An `extra`
    directory is searched with the same globs; an `extra` file is taken as-is.
    """
    home = pathlib.Path.home()
    if sys.platform == "win32":
        files, roots = _roots_windows(home)
    elif sys.platform == "darwin":
        files, roots = _roots_macos(home)
    else:
        files, roots = _roots_linux(home)

    candidates = list(files)
    for root in roots:
        candidates.extend(_expand(root))
    for item in extra:
        item = pathlib.Path(item)
        candidates.extend(_expand(item) if item.is_dir() else [item])

    seen = set()
    out = []  # type: List[Target]
    for path in candidates:
        if _skipped(path):
            continue
        try:
            if not path.is_file():
                continue
            real = path.resolve()
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        kind, product = _classify(real)
        out.append(Target(path=real, kind=kind, product=product))
    return out


# --------------------------------------------------------------------------- patch


def inspect(path: pathlib.Path) -> str:
    """Read-only state, for the status table drawn before anything is touched.

    Returns "patched" | "stock" | "mixed" | "no-signature" | "unreadable".
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return "unreadable"
    scan = _scan(data)
    if scan is None:
        return "no-signature"
    if all(not stock for stock, _ in scan):
        return "patched"
    if all(done == 0 for _, done in scan):
        return "stock"
    return "mixed"


def patch_file(path: pathlib.Path, dry_run: bool = False) -> Tuple[str, int, str]:
    """Apply the renames in place. Returns (code, replaced, detail).

    Scans the whole file before writing a single byte, so a build missing either
    signature is left untouched even when the other pair matched. Writes land
    only at the matched offsets: a seek-write of ten bytes survives an interrupt,
    a whole-file rewrite of 200 MB does not.
    """
    mode = "rb" if dry_run else "r+b"
    try:
        with open(path, mode) as f:
            data = f.read()
            scan = _scan(data)
            if scan is None:
                return "no-signature", 0, ""
            replaced = sum(len(stock) for stock, _ in scan)
            if not replaced:
                return "already", 0, ""
            if dry_run:
                return "patched", replaced, ""
            for (_old, new), (offsets, _done) in zip(PAIRS, scan):
                for off in offsets:
                    f.seek(off)
                    f.write(new)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass  # best effort; the bytes are already in the page cache
            return "patched", replaced, ""
    except PermissionError as e:
        return "denied", 0, str(e)
    except OSError as e:
        # The app is holding the file: sharing violation on Windows, ETXTBSY on Unix.
        if getattr(e, "winerror", None) == 32 or (
            _ETXTBSY is not None and e.errno == _ETXTBSY
        ):
            return "locked", 0, str(e)
        return "error", 0, str(e)


def patch(target: Target, dry_run: bool = False) -> Result:
    """patch_file wrapped around a Target.

    dry_run opens the file read-only and reports the code the real run would
    have produced — this is what --check uses.
    """
    code, replaced, detail = patch_file(target.path, dry_run=dry_run)
    return Result(target=target, code=code, replaced=replaced, detail=detail)


# --------------------------------------------------------------------------
# Part 2 — environment persistence and service wiring, per OS.
# --------------------------------------------------------------------------


import os
import pathlib
import subprocess
import sys
from typing import List, Optional, Tuple


ENV_VAR = "AG_LS_PROXY"
LINUX_UNIT = "ag-unlocker-proxy"
WINDOWS_TASK = "AG Unlocker DNS"

_TIMEOUT = 5
_IS_WIN = os.name == "nt"
_IS_MAC = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")
_CLIENT_NAMES = ("antigravity", "agy", "language_server")
_CREATE_NO_WINDOW = 0x08000000

_CONF_HEADER = (
    "# Written by ag-repatch: points the Antigravity language server at the\n"
    "# local unlocker proxy. Delete this file to disable it.\n"
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(argv: List[str]) -> Optional[Tuple[int, str]]:
    """Run argv with no shell and a 5 s cap; None if it timed out or is absent.

    stderr is folded into stdout so an error `detail` needs only one stream.
    """
    kw = {"creationflags": _CREATE_NO_WINDOW} if _IS_WIN else {}
    try:
        p = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_TIMEOUT,
            **kw  # type: ignore[arg-type]
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return p.returncode, p.stdout.decode("utf-8", "replace")


def _conf_path() -> pathlib.Path:
    """Linux persistence file. Read from the env every call so a test can point
    XDG_CONFIG_HOME at a temp dir."""
    base = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "environment.d" / "ag-unlocker.conf"


def _plist_path() -> pathlib.Path:
    """macOS login agent that re-runs `launchctl setenv` at every login."""
    return pathlib.Path.home() / "Library" / "LaunchAgents" / "ag-unlocker-env.plist"


def _win_key(write: bool):
    import winreg  # local: absent on every other OS

    access = winreg.KEY_READ | (winreg.KEY_WRITE if write else 0)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access)


def _win_broadcast() -> None:
    """Tell already-running shells the user environment changed, so an app
    started next sees the variable without a logoff."""
    import ctypes

    try:
        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            0xFFFF, 0x1A, 0, ctypes.c_wchar_p("Environment"), 0x0002, 5000,
            ctypes.byref(ctypes.c_ulong()),
        )
    except Exception:  # cosmetic only — persistence already happened
        pass


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #
def proxy_env_read() -> Optional[str]:
    """The persistently configured AG_LS_PROXY, or None. Ignores the value that
    merely happens to be in this process's environment."""
    try:
        if _IS_WIN:
            import winreg

            with _win_key(False) as k:
                return str(winreg.QueryValueEx(k, ENV_VAR)[0]) or None
        if _IS_MAC:
            import plistlib

            with open(_plist_path(), "rb") as f:
                args = plistlib.load(f).get("ProgramArguments") or []
            return args[-1] if len(args) >= 2 and args[-2] == ENV_VAR else None
        for line in _conf_path().read_text("utf-8").splitlines():
            line = line.strip()
            if line.startswith(ENV_VAR + "="):
                return line.split("=", 1)[1].strip() or None
    except (OSError, ValueError, KeyError, IndexError):
        return None
    return None


def proxy_env_apply(url: str) -> str:
    """Persist AG_LS_PROXY=url for this user and push it into the live session.

    "applied" | "already" | "denied" | "unsupported" | "error:<detail>"
    """
    if not (_IS_WIN or _IS_MAC or _IS_LINUX):
        return "unsupported"
    same = proxy_env_read() == url
    try:
        if _IS_WIN:
            import winreg

            if not same:
                with _win_key(True) as k:
                    winreg.SetValueEx(k, ENV_VAR, 0, winreg.REG_SZ, url)
            _win_broadcast()
        elif _IS_MAC:
            import plistlib

            _run(["/bin/launchctl", "setenv", ENV_VAR, url])
            if not same:
                p = _plist_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "wb") as f:
                    plistlib.dump(
                        {
                            "Label": "ag-unlocker-env",
                            "ProgramArguments": ["/bin/launchctl", "setenv", ENV_VAR, url],
                            "RunAtLoad": True,
                        },
                        f,
                    )
        else:
            _run(["systemctl", "--user", "set-environment", "%s=%s" % (ENV_VAR, url)])
            if not same:
                p = _conf_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("%s%s=%s\n" % (_CONF_HEADER, ENV_VAR, url), "utf-8")
    except PermissionError:
        return "denied"
    except OSError as e:
        return "error:%s" % e
    return "already" if same else "applied"


def proxy_env_clear() -> str:
    """Undo proxy_env_apply. "applied" when something was removed, "already"
    when there was nothing to remove."""
    if not (_IS_WIN or _IS_MAC or _IS_LINUX):
        return "unsupported"
    had = proxy_env_read() is not None
    try:
        if _IS_WIN:
            import winreg

            if had:
                with _win_key(True) as k:
                    winreg.DeleteValue(k, ENV_VAR)
            _win_broadcast()
        elif _IS_MAC:
            _run(["/bin/launchctl", "unsetenv", ENV_VAR])
            _plist_path().unlink(missing_ok=True)
        else:
            _run(["systemctl", "--user", "unset-environment", ENV_VAR])
            _conf_path().unlink(missing_ok=True)
    except PermissionError:
        return "denied"
    except OSError as e:
        return "error:%s" % e
    return "applied" if had else "already"


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #
def service_status() -> str:
    """State of the unlocker's proxy service.

    "running" | "stopped" | "absent" | "unknown". macOS has no such service and
    always answers "absent" — there is nothing to invent there.
    """
    if _IS_MAC:
        return "absent"
    if _IS_WIN:
        r = _run(["schtasks.exe", "/query", "/tn", WINDOWS_TASK, "/fo", "list"])
        if r is None:
            return "unknown"
        if r[0] != 0:
            return "absent"
        return "running" if "running" in r[1].lower() else "stopped"
    if _IS_LINUX:
        r = _run(["systemctl", "--user", "is-active", "--quiet", LINUX_UNIT])
        if r is None:
            return "unknown"
        if r[0] == 0:
            return "running"
        c = _run(["systemctl", "--user", "cat", LINUX_UNIT])
        if c is None:
            return "unknown"
        return "stopped" if c[0] == 0 else "absent"
    return "unknown"


def service_start() -> str:
    """Start the proxy service if it exists and is not already up.

    "started" | "already" | "absent" | "error:<detail>"
    """
    state = service_status()
    if state == "running":
        return "already"
    if state in ("absent", "unknown"):
        return "absent" if state == "absent" else "error:status-unknown"
    argv = (
        ["schtasks.exe", "/run", "/tn", WINDOWS_TASK]
        if _IS_WIN
        else ["systemctl", "--user", "start", LINUX_UNIT]
    )
    r = _run(argv)
    if r is None:
        return "error:timeout"
    return "started" if r[0] == 0 else "error:%s" % r[1].strip()


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
def running_clients() -> List[str]:
    """Process names of Antigravity/agy/language_server running right now.

    Best effort, never raises: an empty list only means "nothing to warn about",
    it is not a promise that no client holds the binary.
    """
    if _IS_WIN:
        r = _run(["tasklist.exe", "/fo", "csv", "/nh"])
        names = [ln.split('","')[0].lstrip('"') for ln in (r[1] if r else "").splitlines()]
    else:
        r = _run(["ps", "-Ao", "comm="])
        names = [os.path.basename(ln.strip()) for ln in (r[1] if r else "").splitlines()]
    out = []
    for n in names:
        low = n.lower()
        if n not in out and any(m in low for m in _CLIENT_NAMES):
            out.append(n)
    return sorted(out)


def is_admin() -> bool:
    """True when this process can write machine-wide locations (root / elevated)."""
    try:
        if _IS_WIN:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        return os.geteuid() == 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Part 3 — tui.py: the entire visible surface.
# --------------------------------------------------------------------------


import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


MIN_WIDTH = 60    # below this the table degrades to a plain list
MAX_WIDTH = 100   # a 300-column terminal should not get a 300-column rule

# --------------------------------------------------------------------------
# strings — every key MUST exist in both tables (checked by the demo below and
# by part 4's selftest).  Keep them short; they are laid out in columns.
# --------------------------------------------------------------------------

STRINGS = {
    "en": {
        "banner.tagline": "same-length rename patch for Antigravity",
        "scan.searching": "looking for Antigravity binaries",
        "scan.found": "found {n} target(s)",
        "scan.none": "nothing found",
        "col.product": "Product",
        "col.state": "State",
        "col.path": "Path",
        "state.patched": "PATCHED",
        "state.stock": "STOCK",
        "state.mixed": "MIXED",
        "state.no_signature": "NO SIG",
        "state.unreadable": "UNREADABLE",
        "state.already": "ALREADY",
        "state.locked": "LOCKED",
        "state.denied": "DENIED",
        "state.error": "ERROR",
        "res.patched": "patched, {n} spot(s) rewritten",
        "res.already": "already patched, nothing to do",
        "res.no_signature": "unknown build, left untouched",
        "res.locked": "file is in use, close Antigravity and retry",
        "res.denied": "no write permission, retry with elevated rights",
        "res.error": "failed: {detail}",
        "warn.running": "running right now: {names}",
        "warn.admin": "not elevated, system-wide installs may refuse",
        "env.applied": "AG_LS_PROXY set to {url}",
        "env.already": "AG_LS_PROXY already set to {url}",
        "env.failed": "could not set AG_LS_PROXY: {detail}",
        "env.denied": "no permission to set AG_LS_PROXY",
        "env.cleared": "AG_LS_PROXY removed",
        "env.title": "Environment",
        "res.title": "Results",
        "env.unsupported": "this OS has no supported way to persist AG_LS_PROXY",
        "svc.running": "proxy service is running",
        "svc.stopped": "proxy service is installed but stopped",
        "svc.absent": "proxy service is not installed",
        "svc.started": "proxy service started",
        "svc.unknown": "proxy service state unknown",
        "done.restart": "restart Antigravity for the changes to take effect",
        "done.nothing": "nothing changed",
        "confirm.patch": "patch {n} file(s)?",
        "confirm.yn_yes": "[Y/n]",
        "confirm.yn_no": "[y/N]",
        "choose.hint": "arrows move, space toggles, enter confirms",
        "hint.check": "--check reports the state without touching anything",
        "hint.paths": "pass extra paths as arguments if a build was missed",
        "section.scan": "Targets",
        "section.patch": "Patching",
        "section.env": "Environment",
        "section.done": "Done",
    },
    "ru": {
        "banner.tagline": "патч Antigravity переименованием той же длины",
        "scan.searching": "ищу бинарники Antigravity",
        "scan.found": "найдено целей: {n}",
        "scan.none": "ничего не найдено",
        "col.product": "Продукт",
        "col.state": "Состояние",
        "col.path": "Путь",
        "state.patched": "ПРОПАТЧЕН",
        "state.stock": "ОРИГИНАЛ",
        "state.mixed": "СМЕШАНО",
        "state.no_signature": "НЕТ СИГН",
        "state.unreadable": "НЕ ЧИТАЕТСЯ",
        "state.already": "УЖЕ",
        "state.locked": "ЗАНЯТ",
        "state.denied": "НЕТ ПРАВ",
        "state.error": "ОШИБКА",
        "res.patched": "пропатчен, переписано мест: {n}",
        "res.already": "уже пропатчен, делать нечего",
        "res.no_signature": "незнакомая сборка, не тронут",
        "res.locked": "файл занят, закройте Antigravity и повторите",
        "res.denied": "нет прав на запись, повторите с повышенными правами",
        "res.error": "не удалось: {detail}",
        "warn.running": "сейчас запущено: {names}",
        "warn.admin": "нет прав администратора, системные установки могут не поддаться",
        "env.applied": "AG_LS_PROXY установлен в {url}",
        "env.already": "AG_LS_PROXY уже указывает на {url}",
        "env.failed": "не удалось задать AG_LS_PROXY: {detail}",
        "env.denied": "нет прав, чтобы задать AG_LS_PROXY",
        "env.cleared": "AG_LS_PROXY удалён",
        "env.title": "Окружение",
        "res.title": "Результаты",
        "env.unsupported": "на этой ОС нет поддерживаемого способа сохранить AG_LS_PROXY",
        "svc.running": "служба прокси работает",
        "svc.stopped": "служба прокси установлена, но остановлена",
        "svc.absent": "служба прокси не установлена",
        "svc.started": "служба прокси запущена",
        "svc.unknown": "состояние службы прокси неизвестно",
        "done.restart": "перезапустите Antigravity, чтобы изменения вступили в силу",
        "done.nothing": "ничего не изменилось",
        "confirm.patch": "пропатчить файлов: {n}?",
        "confirm.yn_yes": "[Д/н]",
        "confirm.yn_no": "[д/Н]",
        "choose.hint": "стрелки — выбор, пробел — отметить, enter — подтвердить",
        "hint.check": "--check показывает состояние, ничего не меняя",
        "hint.paths": "передайте пути аргументами, если сборка не найдена",
        "section.scan": "Цели",
        "section.patch": "Патчинг",
        "section.env": "Окружение",
        "section.done": "Готово",
    },
}  # type: Dict[str, Dict[str, str]]

# code -> (string key, colour).  Result codes and inspect states share the
# namespace on purpose: "patched" means the same thing from either side.
_BADGES = {
    "patched": ("state.patched", "green"),
    "already": ("state.already", "cyan"),
    "stock": ("state.stock", "yellow"),
    "mixed": ("state.mixed", "yellow"),
    "no-signature": ("state.no_signature", "grey"),
    "unreadable": ("state.unreadable", "red"),
    "locked": ("state.locked", "yellow"),
    "denied": ("state.denied", "red"),
    "error": ("state.error", "red"),
}

_COLORS = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "grey": "\x1b[90m",
}

_GLYPHS_UNICODE = {
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│",
    "ok": "✓", "bad": "✗", "warn": "!", "step": "›", "bullet": "•",
    "ell": "…", "on": "◉", "off": "○", "cursor": "❯",
    "spin": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
}
_GLYPHS_ASCII = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
    "ok": "[ok]", "bad": "[x]", "warn": "[!]", "step": ">", "bullet": "*",
    "ell": "...", "on": "[x]", "off": "[ ]", "cursor": ">",
    "spin": "|/-\\",
}

_ANSI_RE = re.compile("\x1b\\[[0-9;?]*[A-Za-z]")


class _Blank(dict):
    """Formatting map that shows "?" instead of leaking a raw {placeholder}."""

    def __missing__(self, key: str) -> str:
        return "?"


# The characters we must be able to encode to claim unicode_ok.
_PROBE = "".join(sorted(set("".join(v for k, v in _GLYPHS_UNICODE.items()))))


# --------------------------------------------------------------------------
# display width
# --------------------------------------------------------------------------

def _char_width(ch: str) -> int:
    """Columns one character occupies: 0 for combining marks and controls."""
    if unicodedata.combining(ch):
        return 0
    o = ord(ch)
    if o < 32 or 0x7F <= o < 0xA0 or o in (0x200B, 0xFEFF):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Width of `text` in terminal columns, ignoring ANSI escapes."""
    return sum(_char_width(c) for c in _ANSI_RE.sub("", text))


def _take(text: str, width: int, from_left: bool = True) -> str:
    """Longest prefix (or suffix) of `text` that fits in `width` columns."""
    out = []  # type: List[str]
    used = 0
    seq = text if from_left else reversed(text)
    for ch in seq:
        w = _char_width(ch)
        if used + w > width:
            break
        used += w
        out.append(ch)
    return "".join(out) if from_left else "".join(reversed(out))


def middle_elide(text: str, width: int, ell: str = "…") -> str:
    """Squeeze `text` into `width` columns, dropping from the middle.

    Paths are elided, never wrapped: the head (product dir) and the tail
    (binary name) are the parts that identify a target.
    """
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    ew = display_width(ell)
    if width <= ew:
        return _take(ell, width)
    left = (width - ew + 1) // 2
    right = width - ew - left
    return _take(text, left) + ell + _take(text, right, from_left=False)


def _pad(text: str, width: int) -> str:
    """Left-align in `width` display columns (ANSI-safe, never truncates)."""
    return text + " " * max(0, width - display_width(text))


# --------------------------------------------------------------------------
# capability detection
# --------------------------------------------------------------------------

def _enable_windows_vt() -> bool:
    """Turn on ENABLE_VIRTUAL_TERMINAL_PROCESSING; False on <= Win10 1511."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32           # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)         # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _windows_ui_lang() -> Optional[str]:
    try:
        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        return "ru" if (langid & 0x3FF) == 0x19 else "en"
    except Exception:
        return None


def _env_lang() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(var)
        if val:
            return "ru" if val.lower().startswith("ru") else "en"
    if os.name == "nt":
        return _windows_ui_lang() or "en"
    return "en"


def _reconfigure_utf8() -> None:
    """UTF-8 with errors='replace' where available; a mojibake guard."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _encodes(text: str, encoding: Optional[str]) -> bool:
    try:
        text.encode(encoding or "ascii")
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Ui
# --------------------------------------------------------------------------

class Ui:
    """Everything printed by the tool goes through one of these methods."""

    def __init__(self, lang: str, color: bool, unicode_ok: bool, interactive: bool) -> None:
        self.lang = lang if lang in STRINGS else "en"
        self.color = color
        self.unicode_ok = unicode_ok
        self.interactive = interactive
        self.g = dict(_GLYPHS_UNICODE if unicode_ok else _GLYPHS_ASCII)
        self.width = self._terminal_width()

    # -- construction ------------------------------------------------------

    @classmethod
    def detect(cls, lang: Optional[str], force_plain: bool) -> "Ui":
        """Probe the real stream; never assume a capability."""
        out = sys.stdout
        try:
            tty = bool(out.isatty())
        except Exception:
            tty = False
        # Probe the *original* encoding before forcing UTF-8: a cp866 console
        # is exactly where box drawing turns into mojibake.
        enc_ok = _encodes(_PROBE, getattr(out, "encoding", None))
        _reconfigure_utf8()

        color = False
        if not force_plain and tty and not os.environ.get("NO_COLOR"):
            if os.environ.get("TERM", "") != "dumb":
                color = _enable_windows_vt() if os.name == "nt" else True

        unicode_ok = bool(enc_ok) and not force_plain
        try:
            interactive = tty and bool(sys.stdin.isatty())
        except Exception:
            interactive = False
        return cls(lang or _env_lang(), color, unicode_ok, interactive)

    @staticmethod
    def _terminal_width() -> int:
        try:
            cols = shutil.get_terminal_size((80, 24)).columns
        except Exception:
            cols = 80
        return max(20, min(cols, MAX_WIDTH))

    # -- strings -----------------------------------------------------------

    def t(self, key: str, **kw: object) -> str:
        """Translate `key`; a missing key returns the key, never raises."""
        table = STRINGS.get(self.lang, STRINGS["en"])
        text = table.get(key) or STRINGS["en"].get(key) or key
        try:
            return text.format_map(_Blank(kw))
        except (IndexError, ValueError):
            return text

    # -- primitives --------------------------------------------------------

    def _c(self, name: str, text: str) -> str:
        if not self.color or not text:
            return text
        return _COLORS.get(name, "") + text + _COLORS["reset"]

    def _write(self, text: str, err: bool = False) -> None:
        stream = sys.stderr if err else sys.stdout
        try:
            stream.write(text + "\n")
            stream.flush()
        except Exception:
            pass

    # -- blocks ------------------------------------------------------------

    def banner(self) -> None:
        """Framed title.  The frame collapses to a single line if narrow."""
        title = "ag-repatch"
        tagline = self.t("banner.tagline")
        if self.width < MIN_WIDTH:
            self._write(self._c("bold", title) + " " + self._c("dim", tagline))
            return
        inner = self.width - 4          # frame chars plus one space each side
        body = self._c("bold", title) + "  " + self._c("dim", tagline)
        if display_width(body) > inner:  # _pad/_c are ANSI-safe, elide is not
            body = self._c("bold", middle_elide(
                title + "  " + tagline, inner, self.g["ell"]))
        rule = self.g["h"] * (self.width - 2)
        self._write(self._c("blue", self.g["tl"] + rule + self.g["tr"]))
        self._write(self._c("blue", self.g["v"]) + " " + _pad(body, inner) + " "
                    + self._c("blue", self.g["v"]))
        self._write(self._c("blue", self.g["bl"] + rule + self.g["br"]))

    def section(self, key: str, **kw: object) -> None:
        label = self.t(key, **kw)
        rule = self.g["h"] * max(0, self.width - display_width(label) - 3)
        self._write("")
        self._write(self._c("bold", label) + " " + self._c("dim", rule))

    def status(self, code: str) -> str:
        """Code -> coloured badge, e.g. "patched" -> green PATCHED."""
        key, color = _BADGES.get(code.replace("_", "-"), ("state.error", "red"))
        return self._c(color, self.t(key))

    def table(self, rows: Sequence[Tuple[str, str, str]]) -> None:
        """Aligned product/state/path table; column 2 may be a code or text.

        Widths are display widths, so Cyrillic, CJK and combining marks line
        up.  Paths are middle-elided.  Below MIN_WIDTH columns the table
        degrades to a plain per-target list.
        """
        if not rows:
            return
        cells = [(p, self.status(s) if s.replace("_", "-") in _BADGES else s, q)
                 for p, s, q in rows]
        if self.width < MIN_WIDTH:
            for (product, badge, path) in cells:
                self._write("%s %s" % (self._c("bold", product), badge))
                self._write("  " + self._c("dim", middle_elide(
                    path, self.width - 2, self.g["ell"])))
            return

        h_prod, h_state, h_path = (self.t("col.product"), self.t("col.state"),
                                   self.t("col.path"))
        w_prod = min(26, max([display_width(h_prod)]
                             + [display_width(c[0]) for c in cells]))
        w_state = max([display_width(h_state)]
                      + [display_width(c[1]) for c in cells])
        w_path = self.width - 2 - w_prod - w_state - 4
        if w_path < 12:                     # no room for a meaningful path
            w_prod = max(8, w_prod - (12 - w_path))
            w_path = self.width - 2 - w_prod - w_state - 4
        self._write("  " + self._c("dim", "%s  %s  %s" % (
            _pad(h_prod, w_prod), _pad(h_state, w_state), h_path)))
        for (product, badge, path) in cells:
            self._write("  %s  %s  %s" % (
                _pad(middle_elide(product, w_prod, self.g["ell"]), w_prod),
                _pad(badge, w_state),
                self._c("dim", middle_elide(path, w_path, self.g["ell"]))))

    # -- lines -------------------------------------------------------------

    def step(self, key: str, **kw: object) -> None:
        self._write(self._c("cyan", self.g["step"]) + " " + self.t(key, **kw))

    def note(self, key: str, **kw: object) -> None:
        self._write("  " + self._c("dim", self.g["bullet"] + " " + self.t(key, **kw)))

    def warn(self, key: str, **kw: object) -> None:
        self._write(self._c("yellow", self.g["warn"]) + " "
                    + self._c("yellow", self.t(key, **kw)), err=True)

    def error(self, key: str, **kw: object) -> None:
        self._write(self._c("red", self.g["bad"]) + " "
                    + self._c("red", self.t(key, **kw)), err=True)

    def ok(self, key: str, **kw: object) -> None:
        """Success line; same shape as step/note so main can mix them."""
        self._write(self._c("green", self.g["ok"]) + " " + self.t(key, **kw))

    # -- spinner -----------------------------------------------------------

    @contextmanager
    def spinner(self, key: str) -> Iterator[None]:
        """Daemon-thread spinner on \\r.  Silent when not interactive.

        Piping the tool to a log must not produce a megabyte of frames, so
        non-interactive runs print nothing at all here.  Plain mode (no
        colour and no unicode, i.e. --plain) gets no animation either.
        """
        if not self.interactive or not (self.color or self.unicode_ok):
            yield
            return
        label = self.t(key)
        frames = self.g["spin"]
        stop = threading.Event()

        def spin() -> None:
            i = 0
            while not stop.is_set():
                sys.stdout.write("\r%s %s " % (
                    self._c("cyan", frames[i % len(frames)]), label))
                sys.stdout.flush()
                i += 1
                stop.wait(0.08)

        thread = threading.Thread(target=spin, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=0.5)
            sys.stdout.write("\r" + " " * (display_width(label) + 4) + "\r")
            sys.stdout.flush()

    # -- input -------------------------------------------------------------

    def confirm(self, key: str, default: bool = True, **kw: object) -> bool:
        """Y/n prompt.  Non-interactive runs take `default` silently."""
        if not self.interactive:
            return default
        hint = self.t("confirm.yn_yes" if default else "confirm.yn_no")
        prompt = "%s %s %s " % (self._c("cyan", self.g["step"]),
                                self.t(key, **kw), self._c("dim", hint))
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._write("")
            return False
        if not answer:
            return default
        return answer[0] in "yд"

    def choose(self, rows: Sequence[str], preselected: Sequence[int]) -> List[int]:
        """Arrow-key multi-select.  Returns `preselected` if it cannot run.

        Cannot run = not interactive, or the terminal refuses raw mode (a
        dumb terminal, a CI pty-less job).  Never leaves the tty in raw mode.
        """
        picked = sorted({i for i in preselected if 0 <= i < len(rows)})
        if not rows or not self.interactive:
            return list(picked)
        reader = _RawReader()
        if not reader.available():
            return list(picked)

        selected = set(picked)
        cursor = 0
        self._write(self._c("dim", "  " + self.t("choose.hint")))
        for _ in rows:
            self._write("")

        def draw() -> None:
            sys.stdout.write("\x1b[%dA" % len(rows))
            for i, row in enumerate(rows):
                mark = self.g["on"] if i in selected else self.g["off"]
                arrow = self.g["cursor"] if i == cursor else " "
                text = middle_elide(row, self.width - 6, self.g["ell"])
                line = " %s %s %s" % (arrow, mark, text)
                if i == cursor:
                    line = self._c("cyan", line)
                sys.stdout.write("\r\x1b[2K" + line + "\n")
            sys.stdout.flush()

        try:
            with reader:
                draw()
                while True:
                    key = reader.key()
                    if key in ("enter", "q", "esc", "ctrl-c", "eof"):
                        break
                    elif key == "up":
                        cursor = (cursor - 1) % len(rows)
                    elif key == "down":
                        cursor = (cursor + 1) % len(rows)
                    elif key == "space":
                        selected.symmetric_difference_update({cursor})
                    draw()
                if key in ("q", "esc", "ctrl-c", "eof"):
                    return list(picked)
        except Exception:
            return list(picked)
        return sorted(selected)


class _RawReader:
    """Raw single-key reads, cbreak on Unix / msvcrt on Windows.

    Terminal state is always restored in __exit__, including on exception.
    """

    def __init__(self) -> None:
        self._fd = -1
        self._saved = None  # type: object

    def available(self) -> bool:
        if os.name == "nt":
            try:
                import msvcrt  # noqa: F401
                return True
            except Exception:
                return False
        try:
            import termios  # noqa: F401
            import tty      # noqa: F401

            self._fd = sys.stdin.fileno()
            termios.tcgetattr(self._fd)
            return True
        except Exception:
            return False

    def __enter__(self) -> "_RawReader":
        if os.name != "nt":
            import termios
            import tty

            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            mode = termios.tcgetattr(self._fd)   # keys must not echo over the list
            mode[3] &= ~termios.ECHO             # lflag
            termios.tcsetattr(self._fd, termios.TCSANOW, mode)
        return self

    def __exit__(self, *exc: object) -> None:
        if os.name != "nt" and self._saved is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except Exception:
                pass

    def key(self) -> str:
        """One logical key: up/down/space/enter/esc/q/ctrl-c/eof/other."""
        if os.name == "nt":
            import msvcrt

            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                code = msvcrt.getwch()
                return {"H": "up", "P": "down"}.get(code, "other")
        else:
            ch = sys.stdin.read(1)
            if ch in ("", "\x04"):  # closed / ctrl-d: cancel, never spin
                return "eof"
            if ch == "\x1b":
                nxt = sys.stdin.read(1)
                if nxt != "[":
                    return "esc"
                return {"A": "up", "B": "down"}.get(sys.stdin.read(1), "other")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        if ch == "\x03":
            return "ctrl-c"
        if ch.lower() in ("q", "й"):
            return "q"
        return "other"


# --------------------------------------------------------------------------
# demo — inert once the parts are concatenated (needs AG_TUI_DEMO=1)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Part 4 — internal checks (--selftest). Plain asserts, no framework.
# --------------------------------------------------------------------------


import importlib
import os
import pathlib
import shutil
import sys
import tempfile
import types
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Names each part must expose for the checks that use it.
_CORE_NAMES = ("PAIRS", "Target", "find_targets", "inspect", "patch", "patch_file")
_TUI_NAMES = ("Ui",)

# Every string key part 3 promises, in both languages.
_UI_KEYS = (
    "banner.tagline", "scan.searching", "scan.found", "scan.none",
    "col.product", "col.state", "col.path",
    "state.patched", "state.stock", "state.mixed", "state.no_signature",
    "state.unreadable",
    "res.patched", "res.already", "res.no_signature", "res.locked",
    "res.denied", "res.error",
    "warn.running", "warn.admin",
    "env.applied", "env.already", "env.failed",
    "svc.running", "svc.stopped", "svc.absent", "svc.started",
    "done.restart", "done.nothing",
    "confirm.patch", "hint.check", "hint.paths",
)

# Superfluous keys are ignored by both str.format and %-formatting, so one bag
# satisfies every key that takes a placeholder without guessing which does.
_UI_KW = {
    "count": 2, "n": 2, "total": 2, "found": 2,
    "path": "/tmp/x", "product": "Antigravity", "name": "agy",
    "state": "stock", "code": "patched", "detail": "EACCES", "error": "EACCES",
    "url": "http://127.0.0.1:53129", "proxy": "http://127.0.0.1:53129",
    "lang": "en", "version": "1", "clients": "agy",
}


def _part(module: str, names: Sequence[str]) -> Any:
    """Return a namespace exposing `names` from `module`.

    After the integrator concatenates the parts into one file those names are
    already in our globals; standalone they still live in their own module next
    to this file. Resolved at call time so this file imports cleanly either way.
    """
    g = globals()
    if all(n in g for n in names):
        return types.SimpleNamespace(**dict((n, g[n]) for n in names))
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in g else ""
    if here and here not in sys.path:
        sys.path.insert(0, here)
    mod = importlib.import_module(module)
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        raise ImportError("%s lacks %s" % (module, ", ".join(missing)))
    return mod


# --------------------------------------------------------------------------- fixtures


def _filler(n: int, seed: int = 0) -> bytes:
    """Deterministic non-ASCII-text bytes that cannot spell either signature."""
    return bytes(bytearray(((i + seed) * 37 + 11) & 0xFF for i in range(n)))


def _write(path: pathlib.Path, data: bytes) -> bytes:
    with open(str(path), "wb") as f:
        f.write(data)
    return data


def _read(path: pathlib.Path) -> bytes:
    with open(str(path), "rb") as f:
        return f.read()


def _expected(data: bytes, pairs: Sequence[Tuple[bytes, bytes]]) -> bytes:
    """What the file must look like after a full patch."""
    for old, new in pairs:
        data = data.replace(old, new)
    return data


def _offsets(data: bytes, needle: bytes) -> List[int]:
    out = []  # type: List[int]
    i = data.find(needle)
    while i != -1:
        out.append(i)
        i = data.find(needle, i + len(needle))
    return out


def _target(core: Any, path: pathlib.Path) -> Any:
    return core.Target(path=path, kind="language-server", product="unknown")


def _stock_blob(pairs: Sequence[Tuple[bytes, bytes]]) -> bytes:
    """One stock occurrence of every pair, padded so nothing sits at an edge."""
    out = bytearray(_filler(64))
    for i, (old, _new) in enumerate(pairs):
        out += old
        out += _filler(48, seed=i + 1)
    return bytes(out)


# --------------------------------------------------------------------------- checks


def _case1_patch_is_surgical(core: Any, tmp: pathlib.Path) -> None:
    """Stock blob -> "patched", same length, bytes outside the ranges identical."""
    pairs = list(core.PAIRS)
    assert pairs, "PAIRS is empty"
    for old, new in pairs:
        assert len(old) == len(new), "pair changes length: %r -> %r" % (old, new)

    path = tmp / "case1"
    orig = _write(path, _stock_blob(pairs))
    assert core.inspect(path) == "stock", "fresh blob should inspect as stock"

    res = core.patch(_target(core, path))
    assert res.code == "patched", "expected patched, got %r (%s)" % (res.code, res.detail)
    assert res.replaced == len(pairs), "replaced=%d, expected %d" % (res.replaced, len(pairs))

    after = _read(path)
    assert len(after) == len(orig), "length changed: %d -> %d" % (len(orig), len(after))
    assert after == _expected(orig, pairs), "patched bytes are not the expected rewrite"

    # Spelled out rather than inferred from the equality above: every byte
    # outside a rewritten range is the byte the updater left there.
    touched = set()
    for old, _new in pairs:
        for off in _offsets(orig, old):
            touched.update(range(off, off + len(old)))
    for i in range(len(orig)):
        if i not in touched:
            assert after[i] == orig[i], "byte %d outside a match was rewritten" % i
    assert core.inspect(path) == "patched", "patched file should inspect as patched"


def _case2_second_run_is_a_noop(core: Any, tmp: pathlib.Path) -> None:
    """A second patch of the same file reports "already" and writes nothing."""
    path = tmp / "case2"
    _write(path, _stock_blob(core.PAIRS))
    first = core.patch(_target(core, path))
    assert first.code == "patched", "setup: first run gave %r" % first.code
    before = _read(path)

    res = core.patch(_target(core, path))
    assert res.code == "already", "expected already, got %r (%s)" % (res.code, res.detail)
    assert res.replaced == 0, "already must rewrite nothing, got %d" % res.replaced
    assert _read(path) == before, "file changed on an already-patched run"


def _case3_partial_match_writes_nothing(core: Any, tmp: pathlib.Path) -> None:
    """The trap: one pair present, the other absent -> "no-signature", untouched.

    An unrecognised build must be left exactly as the updater left it, so
    Antigravity launches and shows its own error instead of a corrupted one.
    """
    pairs = list(core.PAIRS)
    blobs = {"case3-none": _filler(256, seed=5)}  # type: Dict[str, bytes]
    for i, (old, new) in enumerate(pairs):
        # Only pair i present — as stock, and again as already-patched.
        blobs["case3-only%d-stock" % i] = _filler(40) + old + _filler(40, seed=3)
        blobs["case3-only%d-patched" % i] = _filler(40) + new + _filler(40, seed=3)

    if len(pairs) < 2:
        # With a single pair there is no partial case to trap.
        blobs = {"case3-none": blobs["case3-none"]}

    for name, blob in sorted(blobs.items()):
        path = tmp / name
        orig = _write(path, blob)
        assert core.inspect(path) == "no-signature", \
            "%s should inspect as no-signature" % name
        res = core.patch(_target(core, path))
        assert res.code == "no-signature", \
            "%s: expected no-signature, got %r (%s)" % (name, res.code, res.detail)
        assert res.replaced == 0, "%s: replaced=%d on no-signature" % (name, res.replaced)
        assert _read(path) == orig, "%s: file was written to despite no-signature" % name


def _case4_dry_run_predicts_and_writes_nothing(core: Any, tmp: pathlib.Path) -> None:
    """--check reports the code the real run would give, byte-for-byte inert."""
    pairs = list(core.PAIRS)
    cases = [
        ("case4-stock", _stock_blob(pairs), "patched"),
        ("case4-patched", _expected(_stock_blob(pairs), pairs), "already"),
        ("case4-nosig", _filler(256, seed=9), "no-signature"),
    ]
    for name, blob, expect in cases:
        path = tmp / name
        orig = _write(path, blob)
        dry = core.patch(_target(core, path), dry_run=True)
        assert dry.code == expect, \
            "%s: dry run said %r, expected %r (%s)" % (name, dry.code, expect, dry.detail)
        assert _read(path) == orig, "%s: dry run wrote to the file" % name
        real = core.patch(_target(core, path))
        assert real.code == dry.code, \
            "%s: real run said %r, dry run said %r" % (name, real.code, dry.code)


def _case5_every_occurrence_at_any_offset(core: Any, tmp: pathlib.Path) -> None:
    """Occurrences at byte 0, twice adjacently, and ending exactly at EOF."""
    pairs = list(core.PAIRS)
    first_old = pairs[0][0]
    last_old = pairs[-1][0]
    body = bytearray()
    body += first_old            # at byte 0
    body += first_old            # twice adjacently
    body += _filler(50, seed=2)
    for old, _new in pairs:
        body += old
        body += _filler(17, seed=4)
    body += last_old             # ends exactly at EOF

    path = tmp / "case5"
    orig = _write(path, bytes(body))
    total = sum(len(_offsets(orig, old)) for old, _new in pairs)
    assert total >= len(pairs) + 3, "fixture lost an occurrence (%d)" % total

    res = core.patch(_target(core, path))
    assert res.code == "patched", "expected patched, got %r (%s)" % (res.code, res.detail)
    assert res.replaced == total, "replaced=%d, expected %d occurrences" % (res.replaced, total)

    after = _read(path)
    assert len(after) == len(orig), "length changed: %d -> %d" % (len(orig), len(after))
    assert after == _expected(orig, pairs), "not every occurrence was rewritten"
    for old, _new in pairs:
        assert old not in after, "stock spelling %r survived" % old
    assert core.patch(_target(core, path)).code == "already", "re-run was not already"


def _case6_extra_dir_is_globbed_and_deduped(core: Any, tmp: pathlib.Path) -> None:
    """find_targets([dir]) finds resources/bin/language_server* once, via symlink too."""
    root = tmp / "case6"
    bindir = root / "resources" / "bin"
    bindir.mkdir(parents=True)
    real = bindir / "language_server_test"
    _write(real, _stock_blob(core.PAIRS))
    try:
        os.chmod(str(real), 0o755)
    except OSError:
        pass  # Windows; irrelevant to discovery

    linked = False
    link = bindir / "language_server_link"
    try:
        os.symlink(str(real), str(link))
        linked = link.is_symlink()
    except (OSError, NotImplementedError, AttributeError):
        pass  # no symlink privilege (Windows without developer mode)

    targets = core.find_targets([root])
    resolved = real.resolve()
    hits = [t for t in targets if t.path == resolved]
    assert hits, "planted language_server_test not found under %s" % root
    assert len(hits) == 1, "deduped path appears %d times" % len(hits)
    assert hits[0].kind == "language-server", "kind=%r" % hits[0].kind
    if linked:
        assert not any(t.path == link for t in targets), \
            "symlink returned unresolved alongside its target"
    else:
        print("    note: symlinks unavailable, dedupe half of case 6 not exercised")


def _case7_both_string_tables_are_complete(tui: Any, tmp: pathlib.Path) -> None:
    """Every key in part 3's list answers in ru and en, non-empty and not the key."""
    for lang in ("ru", "en"):
        ui = tui.Ui.detect(lang=lang, force_plain=True)
        assert ui is not None, "Ui.detect(%r) returned None" % lang
        for key in _UI_KEYS:
            value = ui.t(key, **_UI_KW)
            assert isinstance(value, str), "%s/%s: t() returned %r" % (lang, key, type(value))
            assert value.strip(), "%s/%s: empty string" % (lang, key)
            assert value != key, "%s/%s: missing from the table" % (lang, key)


# --------------------------------------------------------------------------- runner


def selftest() -> int:
    """Run every internal check. 0 on success, 3-worthy non-zero on failure."""
    core = None  # type: Any
    tui = None   # type: Any
    skipped = []  # type: List[str]
    try:
        core = _part("core", _CORE_NAMES)
    except Exception as exc:
        skipped.append("cases 1-6 (core unavailable: %s)" % exc)
    try:
        tui = _part("tui", _TUI_NAMES)
    except Exception as exc:
        skipped.append("case 7 (tui unavailable: %s)" % exc)

    checks = []  # type: List[Tuple[str, Callable[[Any, pathlib.Path], None], Any]]
    if core is not None:
        checks += [
            ("1 patch is surgical", _case1_patch_is_surgical, core),
            ("2 second run is a no-op", _case2_second_run_is_a_noop, core),
            ("3 partial match writes nothing", _case3_partial_match_writes_nothing, core),
            ("4 dry run predicts, writes nothing", _case4_dry_run_predicts_and_writes_nothing, core),
            ("5 every occurrence, any offset", _case5_every_occurrence_at_any_offset, core),
            ("6 extra dir globbed and deduped", _case6_extra_dir_is_globbed_and_deduped, core),
        ]
    if tui is not None:
        checks.append(("7 both string tables complete", _case7_both_string_tables_are_complete, tui))

    failures = []  # type: List[str]
    tmp = tempfile.mkdtemp(prefix="ag-repatch-selftest-")
    try:
        for name, fn, part in checks:
            case_dir = pathlib.Path(tmp) / name.split()[0]
            case_dir.mkdir(parents=True, exist_ok=True)
            try:
                fn(part, case_dir)
            except Exception as exc:
                failures.append(name)
                print("FAIL  case %s" % name)
                print("      %s: %s" % (type(exc).__name__, exc))
            else:
                print("ok    case %s" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for note in skipped:
        print("SKIP  %s" % note)
    print("%d passed, %d failed, %d skipped" % (
        len(checks) - len(failures), len(failures), len(skipped)))
    return 1 if failures else 0


# Standalone only: after the integrator concatenates the parts, the file is no
# longer named selftest.py and part 5's own entry point takes over.


# --------------------------------------------------------------------------
# Part 5 — argument parsing and the run itself.
# --------------------------------------------------------------------------


import argparse


def _parse_args(argv):
    # type: (List[str]) -> argparse.Namespace
    p = argparse.ArgumentParser(
        prog="ag-repatch",
        description="Re-apply the Antigravity region patch after an update.",
        epilog="https://github.com/etosheartem/ag-repatch",
    )
    p.add_argument("paths", nargs="*", metavar="PATH",
                   help="extra install paths to include")
    p.add_argument("--check", action="store_true",
                   help="report state, change nothing")
    p.add_argument("--lang", choices=("ru", "en"),
                   help="force the interface language")
    p.add_argument("--plain", "--no-color", dest="plain", action="store_true",
                   help="ASCII only, no colour, no spinner")
    p.add_argument("-y", "--yes", action="store_true", help="do not prompt")
    p.add_argument("--no-env", dest="no_env", action="store_true",
                   help="patch only; leave AG_LS_PROXY alone")
    p.add_argument("--unset", action="store_true",
                   help="remove AG_LS_PROXY (does not unpatch)")
    p.add_argument("--selftest", action="store_true",
                   help="run the internal checks")
    return p.parse_args(argv)


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return 3 if selftest() else 0

    ui = Ui.detect(lang=args.lang, force_plain=args.plain)
    ui.banner()

    with ui.spinner("scan.searching"):
        targets = find_targets([pathlib.Path(p) for p in args.paths])
        states = [inspect(t.path) for t in targets]

    ui.section("section.scan")
    if not targets:
        ui.error("scan.none")
        ui.note("hint.paths")
        return 2
    ui.step("scan.found", n=len(targets))
    ui.table([(t.product, ui.status(s), str(t.path)) for t, s in zip(targets, states)])

    if args.check:
        ui.section("section.env")
        env = proxy_env_read()
        ui.note("env.already", url=env) if env else ui.note("env.failed", detail="-")
        ui.note("svc." + service_status())
        ui.section("section.done")
        ui.note("hint.paths")
        return 0

    # Warned before the attempt rather than after it fails: "locked" is the one
    # outcome the user can still prevent, and only if they hear about it in time.
    busy = running_clients()
    if busy:
        ui.warn("warn.running", names=", ".join(busy))
    if any(s in ("stock", "mixed") for s in states) and not is_admin():
        if any(not os.access(str(t.path), os.W_OK) for t in targets):
            ui.warn("warn.admin")

    todo = [t for t, s in zip(targets, states) if s in ("stock", "mixed")]
    if todo and not args.yes and not ui.confirm("confirm.patch", n=len(todo)):
        return 0

    ui.section("section.patch")
    results = [patch(t) for t in targets]
    for r in results:
        if r.code == "patched":
            ui.ok("res.patched", n=r.replaced)
        elif r.code == "already":
            ui.note("res.already")
        elif r.code == "no-signature":
            ui.note("res.no_signature")
        else:
            ui.error("res." + r.code, detail=r.detail)
    failed = any(r.code in ("locked", "denied", "error") for r in results)

    if args.unset:
        ui.section("section.env")
        code = proxy_env_clear()
        ui.note("env.cleared") if code in ("applied", "already") \
            else ui.warn("env.failed", detail=code)
    elif not args.no_env:
        ui.section("section.env")
        code = proxy_env_apply(PROXY_URL)
        if code in ("applied", "already"):
            ui.note("env." + code, url=PROXY_URL)
        else:
            ui.warn("env.failed", detail=code)
            failed = True
        service_start()
        ui.note("svc." + service_status())

    ui.section("section.done")
    ui.note("done.restart" if any(r.code == "patched" for r in results)
            else "done.nothing")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
