# Build on the target OS. Windows: portable exe; macOS: .app; Linux: directory.
import sys
from pathlib import Path
from PySide6.QtCore import QLibraryInfo

root = Path(SPECPATH).parent
mac = sys.platform == 'darwin'
win = sys.platform == 'win32'
translations = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
a = Analysis(
    [str(root / 'ag-repatch-gui.py')], pathex=[str(root)],
    datas=[(str(translations / 'qtbase_ru.qm'), 'translations'),
           (str(root / 'LICENSE'), '.'), (str(root / 'packaging/THIRD_PARTY.txt'), '.')],
    hiddenimports=[], excludes=['tkinter'],
)
pyz = PYZ(a.pure)
icon = str(root / 'build' / ('icon.icns' if mac else 'icon.ico'))
if win:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='ag-repatch',
              console=False, icon=icon, upx=False, disable_windowed_traceback=True)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ag-repatch',
              console=False, icon=icon if mac else None, upx=False,
              disable_windowed_traceback=True)
    coll = COLLECT(exe, a.binaries, a.datas, name='ag-repatch', upx=False)
    if mac:
        app = BUNDLE(coll, name='ag-repatch.app', icon=icon,
                     bundle_identifier='io.github.etosheartem.ag-repatch',
                     info_plist={'CFBundleShortVersionString': '2.0.0',
                                 'NSHighResolutionCapable': True,
                                 'CFBundleDevelopmentRegion': 'ru',
                                 'CFBundleLocalizations': ['ru']})
