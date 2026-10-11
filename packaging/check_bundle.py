"""Check that the Linux bundle carries what the Qt xcb platform plugin links.

Usage: python packaging/check_bundle.py dist/ag-repatch

The CI smoke test runs with QT_QPA_PLATFORM=offscreen and the build machine has
the X libraries installed, so neither notices a library the bundle relies on the
host for. Desktops without that library then abort at start with "Could not load
the Qt platform plugin xcb". Here every library the xcb plugin needs must be
bundled, except the ones an AppImage is expected to take from the host.
"""
from pathlib import Path
import subprocess
import sys

# Libraries the host system provides (as in the AppImage excludelist): the C
# library, the graphics driver stack and the X connection itself.
HOST = {
    'ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6', 'libdl.so.2', 'libpthread.so.0',
    'librt.so.1', 'libresolv.so.2', 'libgcc_s.so.1', 'libstdc++.so.6',
    'libGL.so.1', 'libEGL.so.1', 'libGLX.so.0', 'libGLdispatch.so.0', 'libOpenGL.so.0',
    'libdrm.so.2', 'libgbm.so.1', 'libxcb.so.1', 'libX11.so.6', 'libXau.so.6', 'libXdmcp.so.6',
    'libfontconfig.so.1', 'libfreetype.so.6', 'libexpat.so.1', 'libz.so.1',
    'libglib-2.0.so.0', 'libgthread-2.0.so.0', 'libgobject-2.0.so.0', 'libdbus-1.so.3',
    'libbsd.so.0', 'libmd.so.0', 'libpcre.so.3', 'libpcre2-8.so.0', 'libffi.so.8',
    'libsystemd.so.0', 'libcap.so.2', 'libgcrypt.so.20', 'libgpg-error.so.0',
    'liblz4.so.1', 'liblzma.so.5', 'libzstd.so.1', 'libpng16.so.16', 'libbrotlidec.so.1',
    'libbrotlicommon.so.1', 'libbz2.so.1.0', 'libharfbuzz.so.0', 'libgraphite2.so.3',
    'libuuid.so.1', 'libblkid.so.1', 'libmount.so.1', 'libselinux.so.1',
}


def needed(path):
    out = subprocess.run(['readelf', '-d', str(path)], capture_output=True, text=True, check=True).stdout
    return [line.split('[', 1)[1].rstrip(']') for line in out.splitlines() if 'Shared library:' in line]


def main():
    bundle = Path(sys.argv[1] if len(sys.argv) > 1 else 'dist/ag-repatch')
    internal = bundle / '_internal'
    qt = internal / 'PySide6/Qt'
    plugin = qt / 'plugins/platforms/libqxcb.so'
    if not plugin.exists():
        raise SystemExit(f'Не найден плагин {plugin}')
    search = [internal, qt / 'lib']
    bundled = lambda name: next((d / name for d in search if (d / name).exists()), None)
    missing, seen, queue = set(), set(), [plugin]
    while queue:
        lib = queue.pop()
        for name in needed(lib):
            if name in seen:
                continue
            seen.add(name)
            path = bundled(name)
            if path is not None:
                queue.append(path)
            elif name not in HOST:
                missing.add(name)
    if missing:
        print('В сборку не попали библиотеки для Qt xcb:', ', '.join(sorted(missing)))
        raise SystemExit(1)
    print(f'Qt xcb: все {len(seen)} зависимостей есть в сборке или в базовой системе')


if __name__ == '__main__':
    main()
