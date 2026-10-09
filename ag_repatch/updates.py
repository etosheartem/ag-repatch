"""Official release discovery and verified downloads. No silent replacement."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import time

import requests

from . import __version__, REPOSITORY_URL

API = 'https://api.github.com/repos/etosheartem/ag-repatch/releases/latest'


def version_tuple(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Не удалось распознать номер стабильного релиза.')
    return tuple(map(int, value.removeprefix('v').split('.')))


def asset_name():
    arch = platform.machine().lower()
    if sys.platform == 'darwin':
        return 'ag-repatch-macos-' + ('arm64' if arch in ('arm64', 'aarch64') else 'x64') + '.dmg'
    if arch not in ('amd64', 'x86_64'):
        raise ValueError('Для этой архитектуры пока нет готового обновления.')
    if sys.platform == 'win32':
        return 'ag-repatch-windows-x64.exe'
    if sys.platform == 'linux':
        return 'ag-repatch-linux-x64.AppImage' if os.environ.get('APPIMAGE') else 'ag-repatch-linux-x64.tar.gz'
    raise ValueError('Для этой системы нет готового обновления.')


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    name: str
    url: str
    sha256: str
    size: int

    @property
    def newer(self):
        return version_tuple(self.version) > version_tuple(__version__)


def check_release():
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(API, timeout=(5, 15), headers={'Accept': 'application/vnd.github+json'})
        response.raise_for_status()
        data = response.json()
    tag = data['tag_name']
    version_tuple(tag)
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('Ожидался опубликованный стабильный релиз.')
    name = asset_name()
    for asset in data['assets']:
        if asset['name'] == name:
            expected = REPOSITORY_URL + '/releases/download/' + tag + '/' + name
            checksum = asset.get('digest', '') or ''
            if asset['browser_download_url'] != expected or not re.fullmatch(r'sha256:[a-f0-9]{64}', checksum):
                raise ValueError('Не удалось проверить источник или контрольную сумму обновления.')
            size = asset['size']
            if not isinstance(size, int) or not 0 < size <= 512 * 1024 * 1024:
                raise ValueError('Некорректный размер обновления.')
            return Release(tag, (data.get('body') or '')[:12000], name, expected, checksum[7:], size)
    raise ValueError('В релизе нет файла для вашей системы.')


def download_release(release, directory):
    if release.url != REPOSITORY_URL + '/releases/download/' + release.version + '/' + release.name:
        raise ValueError('Неверный источник обновления.')
    version_tuple(release.version)
    if release.name != asset_name() or not re.fullmatch('[a-f0-9]{64}', release.sha256):
        raise ValueError('Неверный файл обновления.')
    directory = Path(directory) / release.version
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=directory)
    target = directory / release.name
    total, checksum, deadline = 0, hashlib.sha256(), time.monotonic() + 600
    try:
        with os.fdopen(fd, 'wb') as output, requests.Session() as session:
            session.trust_env = False
            with session.get(release.url, timeout=(5, 15), stream=True) as response:
                response.raise_for_status()
                for chunk in response.iter_content(256 * 1024):
                    total += len(chunk)
                    if total > release.size or time.monotonic() > deadline:
                        raise ValueError('Превышен размер файла или время загрузки.')
                    output.write(chunk)
                    checksum.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != release.size or checksum.hexdigest() != release.sha256:
            raise ValueError('Контрольная сумма не совпала. Загруженный файл удалён.')
        if target.suffix == '.AppImage':
            Path(temporary).chmod(0o755)
        os.replace(temporary, target)
        return target
    finally:
        Path(temporary).unlink(missing_ok=True)


def verify_download(path, release):
    checksum = hashlib.sha256()
    total = 0
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(256 * 1024), b''):
            total += len(block)
            checksum.update(block)
    if total != release.size or checksum.hexdigest() != release.sha256:
        raise ValueError('Файл изменён после загрузки. Скачайте обновление ещё раз.')


def extract_linux_archive(path, destination):
    """Extract only regular files, directories and links contained in this app."""
    import shutil
    import tarfile
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            parts = Path(member.name).parts
            if not parts or parts[0] != 'ag-repatch' or '..' in parts or Path(member.name).is_absolute():
                raise ValueError('Архив содержит недопустимый путь.')
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError('Архив содержит ссылку за пределы приложения.')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.issym():
                link = Path(member.linkname)
                if link.is_absolute() or not (target.parent / link).resolve().is_relative_to(destination):
                    raise ValueError('Архив содержит недопустимую ссылку.')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
            elif member.isfile():
                total += member.size
                if total > 1024 * 1024 * 1024:
                    raise ValueError('Распакованный архив слишком большой.')
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o755)
            else:
                raise ValueError('Архив содержит неподдерживаемый тип файла.')
    executable = destination / 'ag-repatch/ag-repatch'
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('В архиве не найдено исполняемое приложение.')
    return executable


def install_update(path, release, mac_destination=None):
    """Install into a persistent per-version directory, retaining the old app."""
    import plistlib
    import shutil
    import subprocess
    verify_download(path, release)
    path = Path(path)
    if path.name.endswith('.tar.gz'):
        destination = Path(tempfile.mkdtemp(prefix='installed-', dir=path.parent))
        try:
            return extract_linux_archive(path, destination)
        except Exception:
            shutil.rmtree(destination)
            raise
    if path.suffix == '.dmg':
        # A user-owned installation avoids requesting permission for /Applications.
        destination = Path(mac_destination or Path.home() / 'Applications/ag-repatch.app')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.ag-update-', dir=destination.parent) as staging:
            mount = Path(staging) / 'volume'
            mount.mkdir()
            result = subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-plist',
                                     '-mountpoint', str(mount), str(path)], check=True, capture_output=True, timeout=60)
            try:
                plistlib.loads(result.stdout)  # Reject malformed tool output.
                source = mount / 'ag-repatch.app'
                if not (source / 'Contents/MacOS/ag-repatch').is_file():
                    raise ValueError('В образе не найдено приложение ag-repatch.')
                staged = Path(staging) / 'ag-repatch.app'
                shutil.copytree(source, staged, symlinks=True)
            finally:
                subprocess.run(['hdiutil', 'detach', str(mount)], check=True, capture_output=True, timeout=60)
            previous = None
            if destination.exists():
                previous = destination.with_name('ag-repatch-previous-' + str(time.time_ns()) + '.app')
                destination.rename(previous)
            try:
                staged.rename(destination)
            except OSError:
                if previous:
                    previous.rename(destination)
                raise
        return destination
    return path
