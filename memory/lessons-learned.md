---
tags:
  - type/lessons
  - project/nastyaorc
  - tech/tauri
  - tech/rust
  - tech/python
  - area/devops
aliases:
  - Nastya Orchestrator Lessons
---

# Nastya Orchestrator — Lessons Learned

Нумерация сквозная. Пиши только неочевидное.

## Категории
- **[Tauri]** — конфиг, sidecars, bundler, updater
- **[PyInstaller]** — spec, hidden imports, frozen-режим
- **[Proxy/VPN]** — обход блокировок, Opera VPN, корп-прокси
- **[Build]** — CI/локальный билд, PortableBuildTools

---

## #1 [Tauri] NSIS `installMode` и `perMachine` — взаимоисключающие (Tauri 2)

**Проблема:** CI упал на первом билде с:
```
"tauri.conf.json" error on `bundle > windows > nsis`:
{"installMode":"currentUser","perMachine":false,...} is not valid under any of the schemas
```

**Причина:** В Tauri 1 оба поля существовали. В Tauri 2 `installMode` объединил всю логику: `"currentUser"` / `"perMachine"` / `"both"`. Указывать `perMachine` одновременно с `installMode` — schema validation error.

**Решение:** Оставить только `"installMode": "currentUser"`, убрать `"perMachine": false`.

**Теги:** #tauri2 #nsis #schema

---

## #2 [Tauri] `beforeBuildCommand` cwd — корень репо, не `src-tauri/`

**Проблема:** `beforeBuildCommand: "npm --prefix ../frontend run build"` падал на CI с:
```
npm error path D:\a\nastyaorchestrator\frontend\package.json (not found)
```

**Причина:** В Tauri 2 `beforeBuildCommand` запускается с **cwd = корень репо** (где `cargo tauri build` вызывается), а не `src-tauri/`. `../frontend` от корня = на уровень выше корня, там ничего нет.

**Решение:** `npm --prefix frontend run build` — без `../`. Путь резолвится относительно корня репо.

**Теги:** #tauri2 #beforeBuildCommand

---

## #3 [Rust/Tauri] `state.lock().unwrap().take()` chain — borrow checker E0597

**Проблема:** Компилятор падал на `src-tauri/src/lib.rs` с:
```
error[E0597]: `state` does not live long enough
  let state = app.state::<SidecarState>();
  if let Some(child) = state.worker.lock().unwrap().take() { ... }
                       ^^^^^ borrow lifetime...
```

Почему-то только на втором обращении (`worker`), первый (`backend`) проходил.

**Причина:** Цепочка `state.worker.lock().unwrap().take()` создаёт временные, которые ссылаются на `state`. Rust не может доказать что temporary lifetime не выходит за lifetime `state` в этой конкретной конфигурации.

**Решение:** Сначала собрать child-ы в локальные переменные, потом kill:
```rust
let backend_child = state.backend.lock().unwrap().take();
let worker_child = state.worker.lock().unwrap().take();
if let Some(child) = backend_child { let _ = child.kill(); }
if let Some(child) = worker_child { let _ = child.kill(); }
```

**Теги:** #rust #borrow-checker #tauri

---

## #4 [Tauri 2] Env-переменные для signing — `TAURI_SIGNING_PRIVATE_KEY`, не `TAURI_PRIVATE_KEY`

**Проблема:** На CI + локально билд падал на последнем шаге:
```
Error A public key has been found, but no private key.
Make sure to set `TAURI_SIGNING_PRIVATE_KEY` environment variable.
```

**Причина:** В Tauri 1 было `TAURI_PRIVATE_KEY` + `TAURI_KEY_PASSWORD`. В Tauri 2 переименовали:
- `TAURI_SIGNING_PRIVATE_KEY` — путь к файлу ключа ИЛИ сырое содержимое (base64)
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` — пароль

**Решение:** Обновить имена в `.github/workflows/release.yml` и в `dev-shell.bat`.

**Теги:** #tauri2 #signing #updater

---

## #5 [Tauri] `shutdown_sidecars` — `CommandChild.kill()` может не убить orphan-процессы

**Проблема:** При закрытии Tauri-приложения через Quit из трея в tasklist оставались `nastya-backend.exe` и `nastya-worker.exe` (иногда по 2-3 копии — накапливались при повторных запусках).

**Причина:** `CommandChild.kill()` убивает только непосредственный дочерний процесс. Если sidecar-процесс в свою очередь породил своих детей, они становятся orphan. Плюс если Tauri главный процесс крашнулся или был убит принудительно — `shutdown_sidecars` не вызывается вообще.

**Решение:** После `child.kill()` добавить страховочный `taskkill /F /T /IM <name>.exe` на Windows:
```rust
#[cfg(windows)]
{
    use std::process::Command;
    for name in &["nastya-backend.exe", "nastya-worker.exe", "opera-proxy-x86_64-pc-windows-msvc.exe"] {
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/IM", name])
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .output();
    }
}
```

`/T` убивает дерево процессов, `/F` — принудительно. `CREATE_NO_WINDOW` — не мигает консолью.

**Теги:** #tauri #sidecar #windows #process

---

## #6 [PyInstaller] `BASE_DIR` во frozen-режиме ломается

**Проблема:** В `backend/core/config.py`:
```python
BASE_DIR = Path(__file__).resolve().parent.parent.parent
```
При PyInstaller onefile `__file__` указывает внутрь `_MEIPASS` (распакованный temp). `parent.parent.parent` уходит вверх в temp, `data/nastya.db` создаётся не там где ожидается.

**Решение:**
```python
import sys
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent  # рядом с .exe
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
```

**Теги:** #pyinstaller #frozen #basedir

---

## #7 [PyInstaller] markitdown hidden imports — `bs4` не `beautifulsoup4`

**Проблема:** При сборке `.spec` с markitdown расширениями:
```
ERROR: Hidden import 'beautifulsoup4' not found
```

**Причина:** PyPI-пакет называется `beautifulsoup4`, но Python-модуль — `bs4`. PyInstaller hiddenimports ожидает **имя модуля**, не имя пакета.

**Решение:** В `build/backend.spec` и `worker.spec` — `"bs4"`. Аналогично:
- `python_multipart` → `multipart`
- `python-docx` → `docx`

**Теги:** #pyinstaller #hiddenimports #package-vs-module

---

## #8 [Cloudflare/Proxy] Корп-прокси в РФ НЕ обходит Cloudflare geoblock

**Проблема:** `codex` CLI при запросе в `https://chatgpt.com/backend-api/codex/responses` получал `403 Forbidden` с плашкой "Unable to load site. If you are using a VPN, try turning it off."

**Причина:** Cloudflare блочит по **source IP**. Корп-прокси `94.103.191.13:3528` — российский хостинг, IP российский, Cloudflare его заносит в блок-лист. Протокол (HTTP/SOCKS) не важен, важен только TCP source IP.

**Решение:** Нужен прокси/VPN с НЕ-российским IP. Самый дешёвый путь — **Opera VPN через `opera-proxy` от Alexey71** (Go binary, 7.5 МБ). Цепочка:
```
app → HTTPS_PROXY=127.0.0.1:18080
  → opera-proxy (local)
  → -proxy корп-прокси (как outbound)
  → Opera API → выдаёт EU endpoint (77.111.247.46)
  → туннелирует через Opera EU
  → chatgpt.com видит EU IP → пропускает
```

Opera-proxy запускается как третий sidecar в Tauri, рядом с backend/worker. Не требует админа, не трогает системные прокси настройки, порт >1024.

**Теги:** #cloudflare #proxy #vpn #opera

---

## #9 [Build] PortableBuildTools — MSVC без админа

**Проблема:** Обычный `vs_BuildTools.exe` от Microsoft требует админ-права для установки. Если их нет — невозможно собрать `cargo tauri build` на Windows.

**Решение:** [Data-Oriented-House/PortableBuildTools](https://github.com/Data-Oriented-House/PortableBuildTools) — утилита, которая скачивает официальные MSVC компоненты напрямую с CDN Microsoft и кладёт в user-profile. Без админа.

**Шаги:**
1. Download `PortableBuildTools.exe`
2. Install path: `D:\BuildTools` (куда хочешь, без админа)
3. Выбор `Create the scripts & add to user environment` — env переменные прописываются в user-profile
4. Targets: x64
5. После установки — **logout/login** или вызов `D:\BuildTools\devcmd.bat` вручную в cmd

**Nuance:** rustup-init проверяет VS через реестр/vswhere — PortableBuildTools туда ничего не пишет. При установке Rust через rustup-init выбирать `3) Don't install prerequisites` — MSVC уже в PATH через `devcmd.bat`, rustc его найдёт.

**Теги:** #msvc #rust #build #no-admin

---

## #10 [Tauri] Configuration changes в `data/` под frozen — нужна БД в установленной папке

**Проблема:** После переустановки приложения БД `data/nastya.db` оставалась со старыми настройками прокси (например корп-прокси `94.103.191.13:3528`), даже если в новой версии дефолт изменился на `127.0.0.1:18080`.

**Причина:** `backend/core/proxy.py` читает настройки из БД при startup. Если БД уже существует — берутся сохранённые значения, не новые дефолты. Новые дефолты применяются только к пустой БД.

**Решение:** При переустановке — удалять `data/` целиком (тестовые данные теряются) ИЛИ через UI Настройки поправить поля → Save.

Для продакшена — миграции настроек: добавить версию схемы `app_settings`, при старте проверять версию и обновлять старые значения.

**Теги:** #pyinstaller #config #migration

---

## #11 [DevOps] Dev GUI — tkinter обёртка для локальных билдов

**Проблема:** После настройки локальной сборки (PortableBuildTools + Rust + tauri-cli) — много ручных шагов: dev-shell → запомнить команды → отдельно `debug-worker.bat` → следить за несколькими cmd окнами → вручную проверять backend/opera-proxy. Нужен централизованный dev-tool.

**Решение:** `dev-gui.pyw` — tkinter GUI в корне проекта, запуск через `dev-gui.bat` (подхватывает MSVC env + signing key).

**Выбор tkinter vs PyQt6:** tkinter выбран потому что встроен в Python, 0 зависимостей, не нужен pip install через корп-прокси. PyQt6/PySide6 даёт более современный вид но +100 МБ install. Альтернатива — `customtkinter` (обёртка tkinter с modern дизайном, 1 pip install).

**Структура:** 3 ряда кнопок (по частям / Tauri / утилиты) + большой лог textbox + status bar. См. `dev-gui.pyw` docstring.

**Nuances:**
- **Цветные индикаторы через `tk.Label(bg=...)`**, НЕ эмодзи 🟢🔴. Segoe UI в Windows 10 рендерит color emoji в tkinter как монохромные круги — сломано визуально. Рабочее: `tk.Label(bg='#4ec9b0')` (НЕ `ttk.Label` — у ttk `bg` не работает в большинстве themes)
- **`creationflags=CREATE_NO_WINDOW`** для subprocess — не мигает консолью при запуске каждой команды
- **Queue-based polling** — stdout читается в thread, кладётся в `queue.Queue`, GUI через `root.after(80, poll)` тянет из очереди. Thread-safe обновление Text widget
- **Только одна команда одновременно** — проверка `self.current_proc.poll()` перед запуском
- **UTF-8 принудительно** через `env["PYTHONIOENCODING"] = "utf-8"` + `encoding="utf-8"` в `subprocess.Popen` — русский читается без кракозябр

**Теги:** #devops #tkinter #build-tool #gui

## Краткая справка по стеку (2026-04-08)

- **Frontend:** React 19 + Vite (TS)
- **Backend:** FastAPI + uvicorn + SQLite (WAL)
- **Worker:** Python, httpx async, subprocess для codex/claude CLI
- **Desktop:** Tauri 2, NSIS per-user installer, WebView2 bootstrapper
- **Бинари в бандле:** 3 sidecar — nastya-backend.exe (75 MB), nastya-worker.exe (70 MB), opera-proxy.exe (7.5 MB)
- **CI:** GitHub Actions windows-latest + cargo tauri build + подпись через minisign
- **Локальный билд:** `local-build.bat` через PortableBuildTools + Rust stable + tauri-cli 2.x
- **Signing:** ключи в `%USERPROFILE%\.tauri\nastya.key` (private) + pubkey в `tauri.conf.json`
