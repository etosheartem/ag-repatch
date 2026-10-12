"""Explicit network checks and a privacy-preserving support report."""
from .i18n import tr
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


def diagnose(proxy_url=None):
    checks = []
    if proxy_url is not None:
        proxy_url = validate_proxy(proxy_url)
        proxy = urlsplit(proxy_url)
        try:
            with socket.create_connection((proxy.hostname, proxy.port or (443 if proxy.scheme == 'https' else 80)), timeout=4):
                pass
        except OSError:
            return [Check(tr('Порт прокси'), False, tr('Нет соединения. Запустите прокси и проверьте адрес и порт в настройках.'))]
        checks.append(Check(tr('Порт прокси'), True, tr('Соединение установлено.')))
    with requests.Session() as session:
        # Use exactly the selected route. Environment proxies, NO_PROXY and
        # .netrc must not override it. Direct requests still use OS VPN routes.
        session.trust_env = False
        session.proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url is not None else {}
        for name, url in [(tr('HTTPS через прокси') if proxy_url is not None else tr('HTTPS через VPN или напрямую'), 'https://example.com/'),
                          (tr('Сервис Google'), 'https://generativelanguage.googleapis.com/')]:
            try:
                with session.get(url, timeout=(5, 8), allow_redirects=False, stream=True) as response:
                    code = response.status_code
                if url == 'https://generativelanguage.googleapis.com/' and code in (401, 404):
                    checks.append(Check(name, True, tr('Сервер ответил (HTTP {0}). Доступ к моделям и авторизация не проверялись.').format(code)))
                elif 200 <= code < 400:
                    checks.append(Check(name, True, tr('Получен ответ HTTP {0}.').format(code)))
                else:
                    hint = tr('Прокси требует авторизации.') if code == 407 else tr('Сервер отказал или временно недоступен. Проверьте подключение и маршрут VPN или прокси.')
                    checks.append(Check(name, False, f'HTTP {code}. {hint}'))
            except requests.exceptions.SSLError:
                checks.append(Check(name, False, tr('Ошибка сертификата TLS. Проверьте дату системы и настройки подключения.')))
            except requests.exceptions.ProxyError:
                checks.append(Check(name, False, tr('Прокси не смог создать HTTPS-туннель. Проверьте его настройки и авторизацию.')))
            except requests.exceptions.Timeout:
                checks.append(Check(name, False, tr('Время ожидания истекло. Проверьте подключение и маршрут VPN или прокси.')))
            except requests.RequestException:
                checks.append(Check(name, False, tr('Соединение прервано. Проверьте подключение и повторите проверку.')))
    return checks


def support_report(snapshot, checks):
    # Allowlist rather than attempted redaction of arbitrary exception/log text.
    # No paths, usernames, proxy addresses, environment, tokens or raw logs.
    data = {'приложение': __version__, 'система': platform.system(),
            'версия_системы': platform.release(), 'архитектура': platform.machine(),
            'установки': [], 'проверки': [{'этап': c.name, 'успешно': c.ok, 'результат': c.detail} for c in checks],
            'примечание': tr('Личные пути, адрес прокси и полный журнал исключены. Отчёт никуда не отправляется.')}
    if snapshot:
        data['установки'] = [{'тип': i.target.kind, 'состояние': i.state,
                            'можно_восстановить': i.restorable, 'запись_доступна': i.writable,
                            'восстановление_после_сбоя': i.recoverable} for i in snapshot.items]
        data['порт_прокси_доступен'] = snapshot.proxy_reachable
        data['есть_запущенные_клиенты'] = bool(snapshot.running)
    return json.dumps(data, ensure_ascii=False, indent=2)


def save_report(path, snapshot, checks):
    atomic_write(path, support_report(snapshot, checks).encode('utf-8'))
