"""Linux permission helper: only the two reversible patch substitutions."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from . import engine
from .filelock import lock_file


def edit_plan(before, after):
    edits = []
    covered = bytearray(before)
    for old, new in engine.PAIRS:
        for destination in (old, new):
            start = 0
            while True:
                offset = after.find(destination, start)
                if offset < 0:
                    break
                current = before[offset:offset + len(destination)]
                if current != destination:
                    if any(c not in (a, b) for c, a, b in zip(current, old, new)):
                        raise ValueError('Изменение выходит за пределы патча.')
                    edits.append([offset, destination.decode('ascii')])
                    covered[offset:offset + len(destination)] = destination
                start = offset + len(destination)
    if bytes(covered) != after:
        raise ValueError('Изменение выходит за пределы патча.')
    return edits


def execute_plan(plan):
    path = Path(plan['path'])
    if not (path.name == 'agy' or path.name.startswith('language_server')):
        raise ValueError('Выберите исполняемый файл agy или language_server.')
    if engine.running_clients():
        raise ValueError('Сначала закройте IDE и сеансы agy.')
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+b') as file:
        lock_file(file)
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Ожидался обычный файл без жёстких ссылок.')
        before = file.read()
        if hashlib.sha256(before).hexdigest() != plan['expected']:
            raise ValueError('Файл изменился. Повторите проверку.')
        after = bytearray(before)
        for offset, text in plan['edits']:
            destination = text.encode('ascii')
            pair = next((p for p in engine.PAIRS if destination in p), None)
            if pair is None or type(offset) is not int or offset < 0 or offset + len(destination) > len(before):
                raise ValueError('Недопустимый участок патча.')
            current = before[offset:offset + len(destination)]
            if any(c not in (a, b) for c, a, b in zip(current, *pair)):
                raise ValueError('Участок файла не соответствует патчу.')
            after[offset:offset + len(destination)] = destination
        after = bytes(after)
        if engine._scan(after) is None:
            raise ValueError('Файл не распознан.')
        current_path = path.stat()
        latest = os.fstat(file.fileno())
        if (current_path.st_dev, current_path.st_ino) != (info.st_dev, info.st_ino) or (
                latest.st_mtime_ns, latest.st_ctime_ns, latest.st_size) != (info.st_mtime_ns, info.st_ctime_ns, info.st_size):
            raise ValueError('Файл изменился во время проверки.')
        from .backend import BackupStore, digest
        try:
            BackupStore._write_differences(file, before, after)
            file.seek(0)
            if digest(file.read()) != digest(after):
                raise OSError('Не пройдена проверка записи.')
        except OSError:
            BackupStore._write_differences(file, after, before)
            raise


def apply_with_permission(store, path, restore=False, recover=False):
    if sys.platform != 'linux' or not shutil.which('pkexec'):
        raise ValueError('Для запроса прав требуется установленный polkit (команда pkexec).')
    path = path.resolve()
    before = path.read_bytes()
    if recover:
        after = store.recovery_original(path, before)
    elif restore:
        after = store.original(path, before)
    else:
        if engine._scan(before) is None:
            raise ValueError('Версия не распознана.')
        after = before
        for old, new in engine.PAIRS:
            after = after.replace(old, new)
        store.prepare(path, before, after)
    if after is None:
        raise ValueError('Нет подходящей резервной копии.')
    if before == after:
        return 'Уже пропатчен'
    from .startup import launch_command
    command = launch_command()
    if os.environ.get('APPIMAGE'):
        command.append('--appimage-extract-and-run')
    command += ['--privileged-patch']
    env = os.environ.copy()
    # Avoid leaking PyInstaller's library search path into system pkexec.
    if 'LD_LIBRARY_PATH_ORIG' in env:
        env['LD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH_ORIG']
    else:
        env.pop('LD_LIBRARY_PATH', None)
    plan = {'path': str(path), 'expected': hashlib.sha256(before).hexdigest(), 'edits': edit_plan(before, after)}
    result = subprocess.run(['pkexec', *command], input=json.dumps(plan), text=True,
                            capture_output=True, timeout=180, env=env)
    if result.returncode:
        raise ValueError('Изменение не выполнено: запрос прав отменён или операция отклонена. ' + result.stdout.strip()[:1000])
    if path.read_bytes() != after:
        raise ValueError('Результат записи не совпал с ожидаемым. Повторите проверку.')
    return 'Оригинал восстановлен' if restore or recover else 'Патч применён, оригинал сохранён'


def main():
    try:
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise ValueError('Для этой операции нужны права администратора.')
        plan = json.loads(sys.stdin.read(1024 * 1024))
        execute_plan(plan)
        return 0
    except Exception as exc:
        print(str(exc))
        return 1
