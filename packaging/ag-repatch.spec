# Build on the target OS. Windows: portable exe; macOS: .app; Linux: directory.
import sys
from pathlib import Path
from PySide6.QtCore import QLibraryInfo

root = Path(SPECPATH).parent
mac = sys.platform == 'darwin'
win = sys.platform == 'win32'
translations = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))


def linux_xcb_libraries():
    # The Qt xcb platform plugin links these, but PyInstaller silently skips a
    # dependency that is missing on the build machine, and many desktops and
    # minimal images do not ship them. Bundle them explicitly so the AppImage
    # starts without extra system packages.
    names = ['libxcb-icccm.so.4', 'libxcb-keysyms.so.1', 'libxcb-shape.so.0']
    dirs = [Path('/usr/lib/x86_64-linux-gnu'), Path('/lib/x86_64-linux-gnu'),
            Path('/usr/lib64'), Path('/usr/lib')]
    found = []
    for name in names:
        path = next((d / name for d in dirs if (d / name).exists()), None)
        if path is None:
            raise SystemExit(f'{name} не найдена: установите libxcb-icccm4, '
                             'libxcb-keysyms1 и libxcb-shape0 перед сборкой')
        found.append((str(path), '.'))  # keep the soname as the bundled file name
    return found


a = Analysis(
    [str(root / 'ag-repatch-gui.py')], pathex=[str(root)],
    binaries=linux_xcb_libraries() if sys.platform.startswith('linux') else [],
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
                     info_plist={'CFBundleShortVersionString': '2.1.2',
                                 'NSHighResolutionCapable': True,
                                 'CFBundleDevelopmentRegion': 'en',
                                 'CFBundleLocalizations': ['en', 'ru']})
