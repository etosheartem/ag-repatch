<div align="center">

# ⚡ ag-repatch

**Instant and safe region patch restorer for Google Antigravity**

Single-file script with zero dependencies. Works seamlessly on Linux, macOS, and Windows.

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Linux%20|%20macOS%20|%20Windows-555555?style=flat-square)](https://github.com/etosheartem/ag-repatch)
[![Dependencies](https://img.shields.io/badge/Dependencies-Zero-2EA44F?style=flat-square)](#)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

**English** · [Русский](README.ru.md)

---

</div>

```
╭──────────────────────────────────────────────────────────────────────────────╮
│ ag-repatch  same-length rename patch for Antigravity                         │
╰──────────────────────────────────────────────────────────────────────────────╯

Targets ───────────────────────────────────────────────────────────────────────
› found 2 target(s)
  Product          State    Path
  Antigravity CLI  STOCK    /home/you/.local/bin/agy
  Antigravity IDE  PATCHED  /opt/antigravity-ide/reso…anguage_server_linux_x64

Patching ──────────────────────────────────────────────────────────────────────
✓ patched, 8 spot(s) rewritten
  • already patched, nothing to do

Environment ───────────────────────────────────────────────────────────────────
✓ AG_LS_PROXY set to http://127.0.0.1:53129
  • proxy service is running

Done ──────────────────────────────────────────────────────────────────────────
  • restart Antigravity for this to take effect
```

---

## 💡 The Problem

Google Antigravity's language server carries a region gate (`ineligible`). Once patched, the application periodically updates itself in the background, restores the stock binary, and silently revives the block.

`ag-repatch` discovers all installed Antigravity components (both IDE and CLI) and re-applies the patch in **under a second**.

---

## 📦 Prerequisites: Installing Python & Antigravity

`ag-repatch` requires only standard Python 3.8+ and an Antigravity installation.

<details open>
<summary><b>🐧 Linux</b></summary>

```bash
# 1. Install Python 3
sudo apt install python3         # Ubuntu / Debian
sudo pacman -S python            # Arch Linux
sudo dnf install python3         # Fedora / RHEL

# 2. Install Antigravity
# Antigravity CLI (agy):
curl -fsSL https://antigravity.google/install.sh | bash

# Antigravity IDE:
# Download .deb / .rpm or tarball from https://antigravity.google
# Arch Linux AUR:
yay -S antigravity-ide-bin
```
</details>

<details open>
<summary><b>🍏 macOS</b></summary>

```bash
# 1. Install Python 3 (via Homebrew or python.org)
brew install python

# 2. Install Antigravity
# Antigravity CLI:
curl -fsSL https://antigravity.google/install.sh | bash

# Antigravity IDE:
brew install --cask antigravity
# Or download .dmg from https://antigravity.google
```
</details>

<details open>
<summary><b>🪟 Windows</b></summary>

```powershell
# 1. Install Python 3 (via winget)
winget install Python.Python.3.12
# If using the python.org installer, make sure to check "Add python.exe to PATH"

# 2. Install Antigravity
# Via winget:
winget install Google.Antigravity
# Or download the installer from https://antigravity.google
```
</details>

---

## 🚀 Quick Start

### 1. Download

```bash
# Clone the repository:
git clone https://github.com/etosheartem/ag-repatch
cd ag-repatch

# Or download the single script directly into PATH:
curl -sSL https://raw.githubusercontent.com/etosheartem/ag-repatch/main/ag-repatch.py -o ~/.local/bin/ag-repatch
chmod +x ~/.local/bin/ag-repatch
```

### 2. Run

```bash
# Safe inspection (read-only, checks binary status):
./ag-repatch.py --check

# Apply the patch:
./ag-repatch.py
```
> **Windows**: run with `py ag-repatch.py --check` and `py ag-repatch.py`.

---

## ⚙️ How It Works

Two surgical, in-place string replacements are applied to the binary:

| Stock | Patched | Purpose |
|---|---|---|
| `ineligible` | `inexigible` | Protobuf descriptor field checked by the client (**main patch**) |
| `https_proxy` | `AG_LS_PROXY` | Isolated proxy environment variable (**routes through unlocker**) |

### 🛡️ Safety Guarantees:
- **Same-length renames**: offsets, relocations, and executable layout remain untouched. The file size does not change by even a single byte.
- **Surgical seek-writes**: full file scan before writing, followed by targeted seek-writes at matched offsets (no risky multi-hundred MB rewrites).
- **Refuses to guess**: if signatures are missing or partially matched, the file is left completely byte-identical.
- **Completely standalone**: zero network access, no background daemons, no telemetry.

---

## 🎛️ CLI Options

```text
ag-repatch [PATH ...]          patch discovered targets and optional paths

  --check                      report status without writing changes
  --lang ru|en                 force UI language
  --plain, --no-color          ASCII only, disable colors and animations
  -y, --yes                    skip confirmation prompt
  --no-env                     patch only; leave AG_LS_PROXY untouched
  --unset                      remove AG_LS_PROXY from the environment
  --selftest                   run internal integrity test suite
```

### Exit Codes:
| Code | Meaning |
|:---:|---|
| `0` | Success / everything is patched and up to date |
| `1` | Access error (file is locked or insufficient permissions) |
| `2` | No Antigravity installation found |
| `3` | `--selftest` assertion failure |

---

## 🔍 Under the Hood

<details>
<summary><b>📂 Binary Search Paths</b></summary>

- **Linux**: `~/.local/bin/agy`, `~/.agy/bin/agy`, `~/.local/share/agy/bin/agy`, and `resources/bin/language_server*` + `resources/app/extensions/antigravity/bin/language_server*` under `/opt/antigravity*`, `/usr/share/antigravity*`, `~/.local/share/antigravity*`.
- **macOS**: search paths inside `/Applications/Antigravity*.app/Contents/Resources` and `~/Applications/...`, plus `agy` in `~/.local/bin`, `/usr/local/bin`, `/opt/homebrew/bin`.
- **Windows**: `%LOCALAPPDATA%\Programs\Antigravity`, `%LOCALAPPDATA%\Programs\Antigravity IDE`, `%LOCALAPPDATA%\agy`, and matching paths under `%PROGRAMFILES%` / `%PROGRAMFILES(X86)%`.
- *Note*: Snap installations are skipped intentionally because snapd mounts them on a read-only squashfs.
</details>

<details>
<summary><b>🌐 AG_LS_PROXY Environment Integration</b></summary>

Sets `AG_LS_PROXY=http://127.0.0.1:53129` (the unlocker's loopback proxy) without polluting system-wide `HTTPS_PROXY`:
- **Linux**: writes to `~/.config/environment.d/ag-unlocker.conf` (for future sessions) and executes `systemctl --user set-environment` (for running sessions).
- **macOS**: runs `launchctl setenv` and registers `~/Library/LaunchAgents/ag-unlocker-env.plist`.
- **Windows**: writes to `HKCU\Environment` via the registry and broadcasts `WM_SETTINGCHANGE`.
</details>

<details>
<summary><b>🧪 Integrity Self-Tests (--selftest)</b></summary>

```bash
./ag-repatch.py --selftest
```
Runs 7 independent assertion checks verifying file length invariance, idempotency, byte-for-byte exactness, dry-run safety, and edge-offset handling.
</details>

---

## 📄 License

Released under the [MIT License](LICENSE).
