"""Exercise update installation of the freshly built payload in a temporary folder."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ag_repatch import __version__
from ag_repatch.updates import Release, install_update

if sys.platform in ('linux', 'darwin'):
    name = 'ag-repatch-macos.dmg' if sys.platform == 'darwin' else 'ag-repatch-linux-x64.tar.gz'
    path = ROOT / 'dist' / name
    data = path.read_bytes()
    release = Release(__version__, '', name, '', hashlib.sha256(data).hexdigest(), len(data))
    with tempfile.TemporaryDirectory(prefix='install-check-', dir=ROOT / 'build') as directory:
        # Copy into the isolated folder: Linux installs alongside the payload.
        import shutil
        local = Path(directory) / name
        shutil.copy2(path, local)
        target = install_update(local, release, Path(directory) / 'ag-repatch.app')
        executable = target / 'Contents/MacOS/ag-repatch' if target.suffix == '.app' else target
        subprocess.run([str(executable), '--smoke-test'], check=True, timeout=30,
                       env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
    print('Установка обновления и запуск проверены.')
