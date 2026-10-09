"""Opt-in per-user login launch. Does not install a privileged service."""
from __future__ import annotations

import os
import pathlib
import plistlib
import subprocess
import sys

from .backend import atomic_write


def launch_command() -> list[str]:
    if sys.platform == "linux" and os.environ.get("APPIMAGE"):
        return [os.path.abspath(os.environ["APPIMAGE"])]
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
    elif sys.platform == "linux":
        path = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "autostart/ag-repatch.desktop"
        if enabled:
            atomic_write(path, desktop_entry(command).encode("utf-8"))
        else:
            path.unlink(missing_ok=True)
    elif enabled:
        raise ValueError("Эта система не поддерживает автозапуск.")


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


def desktop_entry(command, icon="ag-repatch"):
    def quote(value):
        if any(c in value for c in "\n\r\0"):
            raise ValueError("Путь содержит недопустимые символы.")
        value = value.replace("%", "%%")
        for char in ('\\', '"', '`', '$'):
            value = value.replace(char, '\\' + char)
        # Desktop Entry string decoding precedes Exec decoding.
        return '"' + value.replace('\\', '\\\\') + '"'
    return ("[Desktop Entry]\nType=Application\nName=ag-repatch\n"
            "Comment=Antigravity IDE и Gemini через agy\nTerminal=false\n"
            "Categories=Utility;\nExec=" + " ".join(quote(x) for x in command)
            + "\nIcon=" + icon + "\n")


def install_linux_menu(icon_bytes):
    base = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local/share"))
    icon = base / "icons/hicolor/256x256/apps/ag-repatch.png"
    atomic_write(icon, icon_bytes)
    entry = base / "applications/ag-repatch.desktop"
    atomic_write(entry, desktop_entry(launch_command()).encode("utf-8"))
    return entry
