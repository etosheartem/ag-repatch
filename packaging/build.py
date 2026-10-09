"""Run on the target OS: python packaging/build.py."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
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
        import shutil
        stage = build / "dmg"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        shutil.copytree(ROOT / "dist/ag-repatch.app", stage / "ag-repatch.app", symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        subprocess.run(["hdiutil", "create", "-volname", "ag-repatch", "-srcfolder", str(stage),
                        "-ov", "-format", "UDZO", str(ROOT / "dist/ag-repatch-macos.dmg")], check=True)
    print("Готово. Результат находится в папке dist.")


if __name__ == "__main__":
    main()
