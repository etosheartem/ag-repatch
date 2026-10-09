"""Platform discovery, byte patching and environment integration (stdlib only)."""

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
    roots = list(pathlib.Path("/opt").glob("[Aa]ntigravity*"))
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
        if _skipped(real) or real in seen:
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
    only at the matched offsets. This reduces the amount written, but the full
    operation is not atomic. The GUI adds verified backups and rollback.
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
    opener = winreg.CreateKeyEx if write else winreg.OpenKey
    return opener(winreg.HKEY_CURRENT_USER, "Environment", 0, access)


def _win_broadcast() -> None:
    """Tell already-running shells the user environment changed, so an app
    started next sees the variable without a logoff."""
    import ctypes
    from ctypes import wintypes

    try:
        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                         wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                         ctypes.POINTER(ctypes.c_size_t)]
        send.restype = wintypes.LPARAM
        name = ctypes.create_unicode_buffer("Environment")
        result = ctypes.c_size_t()
        send(0xFFFF, 0x1A, 0, ctypes.addressof(name), 0x0002, 5000,
             ctypes.byref(result))
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

            live = _run(["/bin/launchctl", "setenv", ENV_VAR, url])
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
            live = _run(["systemctl", "--user", "set-environment", "%s=%s" % (ENV_VAR, url)])
            if not same:
                p = _conf_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("%s%s=%s\n" % (_CONF_HEADER, ENV_VAR, url), "utf-8")
    except PermissionError:
        return "denied"
    except OSError as e:
        return "error:%s" % e
    if not _IS_WIN and (live is None or live[0] != 0):
        return "error:Настройка сохранена, но текущая сессия не обновлена. " + (live[1].strip() if live else "Команда недоступна или не ответила.")
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
            live = _run(["/bin/launchctl", "unsetenv", ENV_VAR])
            _plist_path().unlink(missing_ok=True)
        else:
            live = _run(["systemctl", "--user", "unset-environment", ENV_VAR])
            _conf_path().unlink(missing_ok=True)
    except PermissionError:
        return "denied"
    except OSError as e:
        return "error:%s" % e
    if not _IS_WIN and (live is None or live[0] != 0):
        return "error:Настройка удалена, но текущая сессия не обновлена. " + (live[1].strip() if live else "Команда недоступна или не ответила.")
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
        # Numeric task state avoids parsing localized schtasks output.
        r = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                  "$t = Get-ScheduledTask -TaskName 'AG Unlocker DNS' -ErrorAction SilentlyContinue; "
                  "if ($null -eq $t) { exit 2 }; [int]$t.State"])
        if r is None:
            return "unknown"
        if r[0] == 2:
            return "absent"
        if r[0] != 0:
            return "unknown"
        return "running" if r[1].strip() == "4" else "stopped"
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
