"""Opt-in per-user login launch. Does not install a privileged service."""
from __future__ import annotations

import os
import pathlib
import plistlib
import subprocess
import sys

from .backend import atomic_write


def launch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    python = pathlib.Path(sys.executable)
    if os.name == "nt" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    script = pathlib.Path(__file__).resolve().parent.parent / "ag-repatch-gui.py"
    return [str(python), str(script)] if script.exists() else [str(python), "-m", "ag_repatch"]


def set_login_launch(enabled: bool) -> None:
    command = launch_command() + ["--background"]
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, "ag-repatch", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    winreg.DeleteValue(key, "ag-repatch")
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        path = pathlib.Path.home() / "Library/LaunchAgents/io.github.etosheartem.ag-repatch.plist"
        if enabled:
            atomic_write(path, plistlib.dumps({"Label": "io.github.etosheartem.ag-repatch",
                                              "ProgramArguments": command, "RunAtLoad": True}))
        else:
            path.unlink(missing_ok=True)
    elif enabled:
        raise ValueError("Автозапуск из приложения доступен в Windows и macOS.")


def elevate_windows() -> bool:
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    command = launch_command() + ["--wait-for-exit"]
    execute = ctypes.windll.shell32.ShellExecuteW
    execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    execute.restype = wintypes.HINSTANCE
    result = execute(None, "runas", command[0], subprocess.list2cmdline(command[1:]), None, 1)
    return bool(result and result > 32)
