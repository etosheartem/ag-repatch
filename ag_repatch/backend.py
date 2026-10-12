"""GUI-independent operations. No Qt dependency; all mutations are explicit."""
from __future__ import annotations

from .i18n import tr
import hashlib
import json
import os
import pathlib
import socket
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from . import engine


def data_directory() -> pathlib.Path:
    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = pathlib.Path.home() / "Library/Application Support"
    else:
        base = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local/share"))
    return base / "ag-repatch"


def atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".ag-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def installation_version(path):
    """Read metadata; never execute an arbitrary user-selected binary."""
    import re
    for parent in list(path.parents)[:6]:
        for candidate in (parent / "package.json", parent / "resources/app/package.json"):
            try:
                if candidate.stat().st_size > 1024 * 1024:
                    continue
                version = json.loads(candidate.read_text("utf-8")).get("version", "")
                if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+[\w.+-]*", version):
                    return version
            except (OSError, ValueError, AttributeError):
                pass
        if re.fullmatch(r"v?\d+\.\d+\.\d+[\w.+-]*", parent.name):
            return parent.name
    return tr("Не определена")


def validate_proxy(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError(tr("Адрес прокси должен быть строкой."))
    url = url.strip()
    try:
        p = urlsplit(url)
        port = p.port
        if (p.scheme not in ("http", "https") or not p.hostname or p.username or p.password
                or p.path not in ("", "/") or p.query or p.fragment or any(c.isspace() for c in url)):
            raise ValueError
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError(tr("Введите адрес вида http://127.0.0.1:53129, без логина и пароля.")) from None
    return url.rstrip("/")


@dataclass
class Settings:
    proxy_url: str = ""
    manage_proxy: bool = False
    auto_check: bool = True
    auto_patch: bool = False
    start_at_login: bool = False
    theme: str = "system"
    extra_paths: list[str] = field(default_factory=list)
    onboarding_done: bool = False

    @classmethod
    def load(cls, directory: pathlib.Path) -> Settings:
        path = directory / "settings.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(tr("Файл настроек повреждён."))
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for key in ("manage_proxy", "auto_check", "auto_patch", "start_at_login", "onboarding_done"):
            if key in values and type(values[key]) is not bool:
                raise ValueError(tr("Некорректная настройка: ") + key)
        if "extra_paths" in values and (not isinstance(values["extra_paths"], list)
                                        or not all(isinstance(x, str) for x in values["extra_paths"])):
            raise ValueError(tr("Некорректный список путей."))
        result = cls(**values)
        result.validate_connection()
        if result.theme not in ("system", "light", "dark"):
            result.theme = "system"
        return result

    def save(self, directory: pathlib.Path) -> None:
        self.validate_connection()
        atomic_write(directory / "settings.json", json.dumps(asdict(self), ensure_ascii=False, indent=2).encode())

    def validate_connection(self) -> None:
        if self.manage_proxy:
            self.proxy_url = validate_proxy(self.proxy_url)
        elif not isinstance(self.proxy_url, str):
            self.proxy_url = ""


def connection_proxy(settings: Settings, saved_proxy: str | None) -> str | None:
    """Disabling proxy management does not remove an existing client setting."""
    return settings.proxy_url if settings.manage_proxy else saved_proxy or None


@dataclass
class Item:
    target: engine.Target
    state: str
    restorable: bool = False
    writable: bool = False
    version: str = field(default_factory=lambda: tr("Не определена"))
    backup_date: str = ""
    recoverable: bool = False


@dataclass
class Snapshot:
    items: list[Item]
    running: list[str]
    proxy_env: str | None
    proxy_reachable: bool
    service: str
    checked_at: str

    @property
    def pending(self) -> list[Item]:
        return [i for i in self.items if i.state in ("stock", "mixed")]


@dataclass
class Outcome:
    title: str
    lines: list[str]
    ok: bool = True
    changed: bool = False


class BackupStore:
    """Content-addressed originals plus a manifest for every path/version.

    Restore accepts only the exact patched hash associated with the original.
    The manifest is committed BEFORE patching, so interrupted writes never lose
    the original. Interrupted writes can be recovered only when every byte matches the verified original or its planned patch.
    """
    def __init__(self, directory: pathlib.Path):
        self.directory = directory / "backups"

    def _folder(self, path: pathlib.Path) -> pathlib.Path:
        return self.directory / digest(os.fsencode(str(path.resolve())))

    def prepare(self, path: pathlib.Path, original: bytes, patched: bytes) -> None:
        before, after = digest(original), digest(patched)
        folder = self._folder(path)
        blob = folder / (before + ".bin")
        if not blob.exists() or digest(blob.read_bytes()) != before:
            atomic_write(blob, original)
        record = {"path": str(path.resolve()), "original": before, "patched": after,
                  "size": len(original), "created": datetime.now().isoformat(timespec="seconds")}
        atomic_write(folder / (after + ".json"), json.dumps(record, ensure_ascii=False, indent=2).encode())

    def original(self, path: pathlib.Path, current: bytes) -> bytes | None:
        key = digest(current)
        record_path = self._folder(path) / (key + ".json")
        if not record_path.exists():
            return None
        try:
            record = json.loads(record_path.read_text("utf-8"))
            before = record["original"]
            if (record["path"] != str(path.resolve()) or record["patched"] != key
                    or not isinstance(before, str) or len(before) != 64
                    or any(c not in "0123456789abcdef" for c in before)):
                return None
            data = (record_path.parent / (before + ".bin")).read_bytes()
            if digest(data) != before or len(data) != len(current) or len(data) != record["size"]:
                return None
            return data
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def backup_date(self, path):
        dates = []
        for record in self._folder(path).glob("*.json"):
            try:
                dates.append(datetime.fromisoformat(json.loads(record.read_text("utf-8"))["created"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return max(dates).strftime("%d.%m.%Y %H:%M") if dates else ""

    def recovery_original(self, path, current):
        """Allow only interrupted writes of our exact byte differences.

        All other bytes must match a hash-verified original. An application
        update or unrelated corruption is never treated as our interrupted write.
        """
        for record_path in self._folder(path).glob("*.json"):
            try:
                record = json.loads(record_path.read_text("utf-8"))
                before = record["original"]
                if (record["path"] != str(path.resolve()) or record["size"] != len(current)
                        or not isinstance(before, str) or len(before) != 64
                        or any(c not in "0123456789abcdef" for c in before)):
                    continue
                original = (record_path.parent / (before + ".bin")).read_bytes()
                if digest(original) != before or len(original) != len(current) or engine._scan(original) is None:
                    continue
                patched = original
                for old, new in engine.PAIRS:
                    patched = patched.replace(old, new)
                if digest(patched) != record["patched"] or current in (original, patched):
                    continue
                valid = True
                for start in range(0, len(original), 65536):
                    a, b, c = original[start:start+65536], patched[start:start+65536], current[start:start+65536]
                    if c == a or c == b:
                        continue
                    if any(z not in (x, y) for x, y, z in zip(a, b, c)):
                        valid = False
                        break
                if valid:
                    return original
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    @staticmethod
    def _write_differences(f, before: bytes, after: bytes) -> None:
        # Compare blocks rather than every byte of a several-hundred-MB server.
        for start in range(0, len(before), 65536):
            a, b = before[start:start + 65536], after[start:start + 65536]
            if a == b:
                continue
            pos = 0
            while pos < len(a):
                if a[pos] == b[pos]:
                    pos += 1
                    continue
                end = pos + 1
                while end < len(a) and a[end] != b[end]:
                    end += 1
                f.seek(start + pos)
                if f.write(b[pos:end]) != end - pos:
                    raise OSError(tr("Не удалось полностью записать изменения."))
                pos = end
        f.flush()
        os.fsync(f.fileno())

    def change(self, path: pathlib.Path, restore: bool = False, recover: bool = False) -> str:
        # r+b preserves inode, executable mode and extended attributes.
        from .filelock import lock_file, open_for_write
        with open_for_write(path) as f:
            lock_file(f)
            initial = os.fstat(f.fileno())
            before = f.read()
            if restore or recover:
                after = self.recovery_original(path, before) if recover else self.original(path, before)
                if after is None:
                    raise ValueError(tr("Нет подходящей копии: файл обновился или был изменён. Откат отменён."))
            else:
                scan = engine._scan(before)
                if scan is None:
                    raise ValueError(tr("Версия не распознана. Файл оставлен без изменений."))
                after = before
                for old, new in engine.PAIRS:
                    if len(old) != len(new):
                        raise ValueError(tr("Некорректная длина замены."))
                    after = after.replace(old, new)
                if after == before:
                    return tr("Уже пропатчен")
                self.prepare(path, before, after)
            latest = os.fstat(f.fileno())
            current_path = path.stat()
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            # CPython on Windows may expose change time in fstat() but birth time
            # in stat(). Compare ctime only between results from the same API.
            if signature(initial) != signature(latest) or signature(latest)[:-1] != signature(current_path)[:-1]:
                raise ValueError(tr("Файл изменился во время проверки. Повторите операцию."))
            try:
                self._write_differences(f, before, after)
                f.seek(0)
                if digest(f.read()) != digest(after):
                    raise OSError(tr("Проверка записанного файла не пройдена."))
            except OSError as exc:
                try:
                    self._write_differences(f, after, before)
                except OSError:
                    raise OSError(tr("Запись и откат не завершены. Оригинал сохранён в папке резервных копий.")) from exc
                raise
        return tr("Оригинал восстановлен") if restore or recover else tr("Патч применён, оригинал сохранён")


class Backend:
    def __init__(self, directory: pathlib.Path | None = None):
        self.directory = directory or data_directory()
        self.backups = BackupStore(self.directory)
        self._cache: dict = {}

    def scan(self, settings: Settings) -> Snapshot:
        targets = engine.find_targets([pathlib.Path(p) for p in settings.extra_paths])
        items = []
        for target in targets:
            try:
                s = target.path.stat()
                key = (str(target.path), s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size)
                cached = self._cache.get(key)
                if cached is None:
                    data = target.path.read_bytes()
                    scan = engine._scan(data)
                    if scan is None:
                        state = "no-signature"
                    elif all(not stock for stock, _ in scan):
                        state = "patched"
                    elif all(done == 0 for _, done in scan):
                        state = "stock"
                    else:
                        state = "mixed"
                    recoverable = self.backups.recovery_original(target.path, data) is not None
                    if recoverable:
                        state = "interrupted"
                    cached = state, self.backups.original(target.path, data) is not None, recoverable
                    self._cache = {k: v for k, v in self._cache.items() if k[0] != str(target.path)}
                    self._cache[key] = cached
                items.append(Item(target, cached[0], cached[1], os.access(target.path, os.W_OK),
                                  installation_version(target.path), self.backups.backup_date(target.path), cached[2]))
            except OSError:
                items.append(Item(target, "unreadable"))
        saved_proxy = engine.proxy_env_read()
        url = connection_proxy(settings, saved_proxy)
        reachable = False
        if url:
            try:
                p = urlsplit(validate_proxy(url))
                with socket.create_connection((p.hostname, p.port or (443 if p.scheme == "https" else 80)), timeout=1):
                    reachable = True
            except (OSError, ValueError):
                pass
        service = engine.service_status() if url == engine.PROXY_URL else "unused"
        return Snapshot(items, engine.running_clients(), saved_proxy, reachable,
                        service, datetime.now().strftime("%H:%M"))

    def apply(self, settings: Settings, targets: list[engine.Target], restore: bool = False, recover: bool = False, privileged: bool = False) -> Outcome:
        busy = engine.running_clients()
        if busy:
            return Outcome(tr("Сначала закройте запущенные клиенты"), [tr("Открыты процессы: ") + ", ".join(busy)], False)
        lines, ok, changed = [], True, False
        for target in targets:
            try:
                if privileged:
                    from .privileged import apply_with_permission
                    result = apply_with_permission(self.backups, target.path, restore, recover)
                else:
                    result = self.backups.change(target.path, restore, recover)
                changed = changed or result != tr("Уже пропатчен")
                lines.append(str(target.path) + "\n" + result)
            except PermissionError:
                ok = False
                lines.append(str(target.path) + tr("\nНет прав на запись. Нужны права администратора или доступ к папке установки."))
            except (OSError, ValueError) as exc:
                ok = False
                lines.append(str(target.path) + "\n" + str(exc))
        self._cache.clear()
        if settings.manage_proxy and not restore and not recover and ok:
            env = self.configure_proxy(settings)
            ok = env.ok
            lines.extend(env.lines)
        clients = "agy" if targets and all(t.kind == "cli" for t in targets) else tr("используемый клиент")
        title = (tr("Оригиналы восстановлены") if restore or recover else tr("Готово — перезапустите ") + clients) if ok else tr("Нужно ваше внимание")
        return Outcome(title, lines or [tr("Нет файлов для изменения.")], ok, changed)

    def configure_proxy(self, settings: Settings) -> Outcome:
        url = validate_proxy(settings.proxy_url)
        # The bundled unlocker service is only relevant to its original address.
        if url == engine.PROXY_URL:
            service = engine.service_start()
            if service.startswith("error:"):
                return Outcome(tr("Не удалось настроить прокси"), [tr("Не удалось запустить службу прокси: ") + service[6:]], False)
        p = urlsplit(url)
        try:
            with socket.create_connection((p.hostname, p.port or (443 if p.scheme == "https" else 80)), timeout=2):
                pass
        except OSError:
            return Outcome(tr("Прокси недоступен — адрес не применён"),
                           [tr("Запустите прокси-клиент и проверьте адрес. Если используете VPN, выключите отдельный прокси в настройках. Уже применённый патч остаётся на месте.")], False)
        result = engine.proxy_env_apply(url)
        ok = result in ("applied", "already")
        lines = [tr("Прокси настроен: ") + url if ok else tr("Не удалось обновить окружение: ") + result]
        return Outcome(tr("Настройка прокси") if ok else tr("Нужно ваше внимание"), lines, ok)

    def clear_proxy(self) -> Outcome:
        result = engine.proxy_env_clear()
        ok = result in ("applied", "already")
        return Outcome(tr("Настройка прокси удалена") if ok else tr("Не удалось удалить настройку"),
                       [tr("Бинарники не изменялись.") if ok else result], ok)
