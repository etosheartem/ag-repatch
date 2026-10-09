"""Run on the target OS: python packaging/build.py."""
import os
import hashlib
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def build_linux(build, png):
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("Готовые Linux-сборки пока поддерживают только x64")
    dist = ROOT / "dist"
    with tarfile.open(dist / "ag-repatch-linux-x64.tar.gz", "w:gz") as archive:
        archive.add(dist / "ag-repatch", arcname="ag-repatch")
    stage = build / "ag-repatch.AppDir"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(dist / "ag-repatch", stage / "usr/lib/ag-repatch", symlinks=True)
    launcher = stage / "AppRun"
    launcher.write_text('#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                        'exec "$HERE/usr/lib/ag-repatch/ag-repatch" "$@"\n')
    launcher.chmod(0o755)
    (stage / "ag-repatch.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=ag-repatch\n"
        "Comment=Восстановление патча Antigravity IDE и agy\n"
        "Exec=ag-repatch\nIcon=ag-repatch\nTerminal=false\nCategories=Development;Utility;\n",
        encoding="utf-8")
    shutil.copy2(png, stage / "ag-repatch.png")
    (stage / ".DirIcon").symlink_to("ag-repatch.png")
    tool = build / "appimagetool-x86_64.AppImage"
    expected = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
    if not tool.exists():
        urllib.request.urlretrieve(
            "https://github.com/AppImage/appimagetool/releases/download/1.9.1/"
            "appimagetool-x86_64.AppImage", tool)
    if hashlib.sha256(tool.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Не совпадает контрольная сумма appimagetool")
    tool.chmod(0o755)
    subprocess.run([str(tool), "--appimage-extract-and-run", "--no-appstream",
                    str(stage), str(dist / "ag-repatch-linux-x64.AppImage")],
                   env={**os.environ, "ARCH": "x86_64"}, check=True)


def main():
    # Windows CI may redirect output using a non-Cyrillic system code page.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtWidgets import QApplication
    from ag_repatch.gui import app_icon
    from PIL import Image

    app = QApplication([])
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    png = build / "icon.png"
    app_icon(1024).pixmap(1024, 1024).save(str(png))
    icon = build / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
    Image.open(png).save(icon)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    str(ROOT / "packaging/ag-repatch.spec")], check=True)
    if sys.platform == "darwin":
        stage = build / "dmg"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        shutil.copytree(ROOT / "dist/ag-repatch.app", stage / "ag-repatch.app", symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        subprocess.run(["hdiutil", "create", "-volname", "ag-repatch", "-srcfolder", str(stage),
                        "-ov", "-format", "UDZO", str(ROOT / "dist/ag-repatch-macos.dmg")], check=True)
    elif sys.platform == "linux":
        build_linux(build, png)
    print("Готово. Результат находится в папке dist.")


if __name__ == "__main__":
    main()
