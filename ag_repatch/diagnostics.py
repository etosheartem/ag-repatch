"""Explicit network checks and a privacy-preserving support report."""
from dataclasses import dataclass
import json
import platform
import socket
from urllib.parse import urlsplit

import requests

from . import __version__
from .backend import validate_proxy, atomic_write


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def diagnose(proxy_url):
    proxy_url = validate_proxy(proxy_url)
    proxy = urlsplit(proxy_url)
    checks = []
    try:
        with socket.create_connection((proxy.hostname, proxy.port or (443 if proxy.scheme == 'https' else 80)), timeout=4):
            pass
    except OSError:
        return [Check('Порт прокси', False, 'Нет соединения. Запустите прокси и проверьте адрес и порт в настройках.')]
    checks.append(Check('Порт прокси', True, 'Соединение установлено.'))
    with requests.Session() as session:
        # Never use NO_PROXY or .netrc: each check must traverse this proxy.
        session.trust_env = False
        session.proxies = {'http': proxy_url, 'https': proxy_url}
        for name, url in [('HTTPS через прокси', 'https://example.com/'),
                          ('Сервис Google', 'https://generativelanguage.googleapis.com/')]:
            try:
                with session.get(url, timeout=(5, 8), allow_redirects=False, stream=True) as response:
                    code = response.status_code
                if name == 'Сервис Google' and code in (401, 404):
                    checks.append(Check(name, True, f'Сервер ответил (HTTP {code}). Доступ к моделям и авторизация не проверялись.'))
                elif 200 <= code < 400:
                    checks.append(Check(name, True, f'Получен ответ HTTP {code}.'))
                else:
                    hint = 'Прокси требует авторизации.' if code == 407 else 'Проверьте маршрут прокси; сервер отказал или временно недоступен.'
                    checks.append(Check(name, False, f'HTTP {code}. {hint}'))
            except requests.exceptions.SSLError:
                checks.append(Check(name, False, 'Ошибка сертификата TLS. Проверьте дату системы и настройки прокси.'))
            except requests.exceptions.ProxyError:
                checks.append(Check(name, False, 'Прокси не смог создать HTTPS-туннель. Проверьте его настройки и авторизацию.'))
            except requests.exceptions.Timeout:
                checks.append(Check(name, False, 'Время ожидания истекло. Проверьте подключение и маршрут прокси.'))
            except requests.RequestException:
                checks.append(Check(name, False, 'Соединение прервано. Проверьте прокси и повторите проверку.'))
    return checks


def support_report(snapshot, checks):
    # Allowlist rather than attempted redaction of arbitrary exception/log text.
    # No paths, usernames, proxy addresses, environment, tokens or raw logs.
    data = {'приложение': __version__, 'система': platform.system(),
            'версия_системы': platform.release(), 'архитектура': platform.machine(),
            'установки': [], 'проверки': [{'этап': c.name, 'успешно': c.ok, 'результат': c.detail} for c in checks],
            'примечание': 'Личные пути, адрес прокси и полный журнал исключены. Отчёт никуда не отправляется.'}
    if snapshot:
        data['установки'] = [{'тип': i.target.kind, 'состояние': i.state,
                            'можно_восстановить': i.restorable, 'запись_доступна': i.writable,
                            'восстановление_после_сбоя': i.recoverable} for i in snapshot.items]
        data['порт_прокси_доступен'] = snapshot.proxy_reachable
        data['есть_запущенные_клиенты'] = bool(snapshot.running)
    return json.dumps(data, ensure_ascii=False, indent=2)


def save_report(path, snapshot, checks):
    atomic_write(path, support_report(snapshot, checks).encode('utf-8'))
