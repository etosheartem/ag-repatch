<div align="center">

# ⚡ ag-repatch

**Мгновенное и безопасное восстановление регионального патча для Google Antigravity**

Автономный однофайловый скрипт без сторонних зависимостей. Поддержка Linux, macOS и Windows.

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Linux%20|%20macOS%20|%20Windows-555555?style=flat-square)](https://github.com/etosheartem/ag-repatch)
[![Dependencies](https://img.shields.io/badge/Dependencies-Zero-2EA44F?style=flat-square)](#)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

[English](README.md) · **Русский**

---

</div>

```
╭──────────────────────────────────────────────────────────────────────────────╮
│ ag-repatch  патч Antigravity переименованием той же длины                    │
╰──────────────────────────────────────────────────────────────────────────────╯

Цели ──────────────────────────────────────────────────────────────────────────
› найдено целей: 2
  Продукт          Состояние  Путь
  Antigravity CLI  ОРИГИНАЛ   /home/you/.local/bin/agy
  Antigravity IDE  ПРОПАТЧЕН  /opt/antigravity-ide/reso…anguage_server_linux_x64

Патчинг ───────────────────────────────────────────────────────────────────────
✓ пропатчен, переписано мест: 8
  • уже пропатчен, делать нечего

Окружение ─────────────────────────────────────────────────────────────────────
✓ AG_LS_PROXY установлен в http://127.0.0.1:53129
  • служба прокси работает

Готово ────────────────────────────────────────────────────────────────────────
  • перезапустите Antigravity, чтобы изменения вступили в силу
```

---

## 💡 В чём проблема?

Language server в Google Antigravity содержит встроенную проверку региона (`ineligible`). При снятии локального гейта приложение со временем автоматически обновляется, перезаписывает бинарник оригинальной версией и доступ снова блокируется.

`ag-repatch` находит все установленные версии Antigravity (IDE и CLI) и восстанавливает патч за **1 секунду**.

---

## 📦 Предварительные требования: Установка Python и Antigravity

Для работы `ag-repatch` требуется только стандартный Python 3.8+ и установленный Antigravity.

<details open>
<summary><b>🐧 Linux</b></summary>

```bash
# 1. Установка Python 3
sudo apt install python3         # Ubuntu / Debian
sudo pacman -S python            # Arch Linux
sudo dnf install python3         # Fedora / RHEL

# 2. Установка Antigravity
# Antigravity CLI (agy):
curl -fsSL https://antigravity.google/install.sh | bash

# Antigravity IDE:
# Скачайте .deb / .rpm или архив с https://antigravity.google
# Для Arch Linux доступен AUR:
yay -S antigravity-ide-bin
```
</details>

<details open>
<summary><b>🍏 macOS</b></summary>

```bash
# 1. Установка Python 3 (через Homebrew или python.org)
brew install python

# 2. Установка Antigravity
# Antigravity CLI:
curl -fsSL https://antigravity.google/install.sh | bash

# Antigravity IDE:
brew install --cask antigravity
# Либо скачайте .dmg с https://antigravity.google
```
</details>

<details open>
<summary><b>🪟 Windows</b></summary>

```powershell
# 1. Установка Python 3 (через winget)
winget install Python.Python.3.12
# При ручной установке с python.org не забудьте отметить «Add python.exe to PATH»

# 2. Установка Antigravity
# Через winget:
winget install Google.Antigravity
# Либо скачайте официальный установщик с https://antigravity.google
```
</details>

---

## 🚀 Быстрый старт

### 1. Скачивание

```bash
# Клонирование репозитория:
git clone https://github.com/etosheartem/ag-repatch
cd ag-repatch

# Либо скачивание одного файла в PATH:
curl -sSL https://raw.githubusercontent.com/etosheartem/ag-repatch/main/ag-repatch.py -o ~/.local/bin/ag-repatch
chmod +x ~/.local/bin/ag-repatch
```

### 2. Запуск

```bash
# Безопасный осмотр (только чтение, покажет статус бинарников):
./ag-repatch.py --check

# Применение патча:
./ag-repatch.py
```
> **Windows**: используйте `py ag-repatch.py --check` и `py ag-repatch.py`.

---

## ⚙️ Что делает патч

В бинарниках language server и CLI выполняются два точечных переименования:

| Оригинал | Патч | Назначение |
|---|---|---|
| `ineligible` | `inexigible` | Поле protobuf-дескриптора регионального гейта (**основной патч**) |
| `https_proxy` | `AG_LS_PROXY` | Изолированная переменная прокси (**включает прокси-маршрут**) |

### 🛡️ Гарантии безопасности:
- **Никаких сдвигов смещений**: длина заменяемых строк идентична оригиналу байт-в-байт. Размер файла и структура исполняемого кода не меняются.
- **Точечная запись**: файл сканируется целиком, затем перезаписываются только найденные смещения (вместо перезаписи сотен мегабайт).
- **Защита от повреждений**: если бинарник не распознан или совпала только часть сигнатур — файл остаётся на 100% нетронутым.
- **Полная автономность**: скрипт не использует сеть, не оставляет фоновых демонов и не собирает телеметрию.

---

## 🎛️ Параметры запуска

```text
ag-repatch [ПУТЬ ...]          пропатчить найденные цели и доп. пути

  --check                      проверить состояние файлов без записи
  --lang ru|en                 принудительный выбор языка
  --plain, --no-color          вывод без цвета и анимаций (ASCII)
  -y, --yes                    применять без подтверждения
  --no-env                     только патч (не менять AG_LS_PROXY)
  --unset                      удалить AG_LS_PROXY из системы
  --selftest                   запустить самотестирование алгоритмов
```

### Коды возврата:
| Код | Значение |
|:---:|---|
| `0` | Успешно выполнено / всё готово |
| `1` | Ошибка доступа (файл заблокирован приложением или нет прав) |
| `2` | Бинарники Antigravity не найдены |
| `3` | Ошибка в `--selftest` |

---

## 🔍 Технические подробности

<details>
<summary><b>📂 Где ищутся бинарники</b></summary>

- **Linux**: `~/.local/bin/agy`, `~/.agy/bin/agy`, `~/.local/share/agy/bin/agy`, а также `resources/bin/language_server*` и `resources/app/extensions/antigravity/bin/language_server*` внутри `/opt/antigravity*`, `/usr/share/antigravity*`, `~/.local/share/antigravity*`.
- **macOS**: пути внутри `/Applications/Antigravity*.app/Contents/Resources` и `~/Applications/...`, а также `agy` в `~/.local/bin`, `/usr/local/bin`, `/opt/homebrew/bin`.
- **Windows**: `%LOCALAPPDATA%\Programs\Antigravity`, `%LOCALAPPDATA%\Programs\Antigravity IDE`, `%LOCALAPPDATA%\agy`, а также аналогичные пути в `%PROGRAMFILES%` / `%PROGRAMFILES(X86)%`.
- *Примечание*: Snap-пакеты пропускаются, так как их файловая система squashfs доступна только для чтения.
</details>

<details>
<summary><b>🌐 Настройка системной переменной AG_LS_PROXY</b></summary>

Инструмент устанавливает `AG_LS_PROXY=http://127.0.0.1:53129` (локальный порт разблокировщика), не затрагивая глобальный `HTTPS_PROXY`:
- **Linux**: записывает в `~/.config/environment.d/ag-unlocker.conf` (для новых сессий) и выполняет `systemctl --user set-environment` (для текущих процессов).
- **macOS**: выполняет `launchctl setenv` и создаёт агент `~/Library/LaunchAgents/ag-unlocker-env.plist`.
- **Windows**: сохраняет в `HKCU\Environment` через реестр и отправляет broadcast-сообщение `WM_SETTINGCHANGE`.
</details>

<details>
<summary><b>🧪 Самотестирование (--selftest)</b></summary>

```bash
./ag-repatch.py --selftest
```
Включает 7 независимых тестов целостности: неизменность длины файла, идемпотентность повторных запусков, корректный откат, отсутствие записи при частичном совпадении и обработку крайних смещений.
</details>

---

## 📄 Лицензия

Распространяется под лицензией [MIT](LICENSE).
