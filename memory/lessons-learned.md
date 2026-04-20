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

## #12 [Backend] asyncio.to_thread + thread-local SQLite = race на Windows → sync-обёртки

**Проблема:** Добавили async-обёртки (`aexecute`/`afetchall` через `asyncio.to_thread`) чтобы не блокировать event loop. На WSL/Linux 232/232 зелёные. На Windows CI — 9 падений `sqlite3.OperationalError: database is locked`.

**Первая попытка фикса (НЕ сработала):** `PRAGMA busy_timeout=30000` (30 сек). Локально прошло, на CI всё равно падало 6 из 9 — это **структурная гонка, не нехватка времени ожидания**.

**Реальная причина:** `asyncio.to_thread` создаёт новый thread на каждый вызов. `threading.local()` connections → каждый thread открывает свою SQLite-connection → каждая имеет свою WAL-транзакцию. Transaction в thread A может не закоммититься к моменту когда thread B пытается write → lock висит пока thread A не вернётся из scope (и то не гарантированно). На Linux WAL прощающий, на Windows — блокирует намертво.

**Рабочее решение:** async-обёртки стали **sync-обёртками** (выполняют метод напрямую, без `to_thread`). Event loop блокируется на SQLite ops 1-10мс — приемлемо для desktop-app на одного пользователя. Thread pool race полностью исчезает.

```python
async def aexecute(self, sql, params=()):
    return self.execute(sql, params)  # sync напрямую, без to_thread
```

**Когда возвращаться к async:** если появится нагрузка (много concurrent SSE клиентов, 100+ rps) — вводить **dedicated writer-thread с очередью** (не thread pool), чтобы все writes сериализовались через одно соединение.

**Маркер проблемы:** `database is locked` на Windows CI при том что локально на Linux/WSL тесты зелёные. Busy_timeout не помогает — значит race, не waiting.

**Теги:** #sqlite #async #windows #ci #thread-pool-race #desktop-app

## #13 [CI] pytest-asyncio версия жёстко связана с pytest major

**Проблема:** Добавил `pytest-asyncio==0.24.0` в requirements.txt. Локально у меня уже был установлен 1.3.0 — не заметил. CI на Windows упал на `pip install -r requirements.txt` за 56с (#7 step).

**Причина:** `pytest-asyncio==0.24.0` требует `pytest<9`. У нас pytest 9.0.3. Для pytest 9+ нужен `pytest-asyncio>=1.0` (актуально — 1.3.0).

**Решение:** При добавлении pytest-asyncio — не копипастить старую версию из примеров, а проверять совместимость через `pip index versions pytest-asyncio` и выбирать версию, совместимую с установленным pytest.

**Принцип:** Для pytest-plugins всегда проверяй compatibility table — они жёстко связаны с core pytest. Маркер: `pip install` падает на первом запуске CI после добавления зависимости.

**Теги:** #pytest #ci #dependencies #version-pinning

## #14 [Frontend] sass-embedded не в devDeps → build тихо ломается (tsc не ловит)

**Проблема:** Создали `SessionsSidebar.scss` (новый файл). `tsc --noEmit` зелёный 0 ошибок. `pytest` зелёный. Коммит + push + tag v33 = CI собирается. Но `npm run build` в CI падает:
```
[vite:css] Preprocessor dependency "sass-embedded" not found
```

**Причина:** Vite видит `.scss` импорт только на этапе build — не на tsc. Если `sass-embedded` (или `sass`) нет в `package.json` devDeps, build валится на первом .scss файле. Tsc проверяет только типы, не resolves CSS-препроцессоры.

**Решение:** При добавлении ПЕРВОГО `.scss` файла в проект — сразу `npm install -D sass-embedded`. Проверь через `grep scss package.json` что попало в devDependencies.

**Обнаружение:** Только через `npm run build` в pre-release проверке. Tsc и dev-режим не ловят (dev через vite не падает т.к. HMR может подставить noop). **Добавить `npm run build` в CI checklist обязательно** — не полагаться на tsc.

**Теги:** #frontend #vite #scss #build #pre-release-check

## #15 [Backend] Новое поле в schema → audit ВСЕХ INSERT мест (не только очевидных)

**Проблема:** Добавили `chat_messages.session_id`. Обновили `INSERT` в `backend/api/chat.py::send_message` (user-сообщения). Тесты зелёные 222/222. Третий прогон rev big нашёл: `backend/api/results.py::submit_result` (assistant-сообщения) **НЕ** передавал session_id → ответ LLM сохранялся с NULL → `loadMessages(session_id)` фильтровал по session_id → через 500ms ответ исчезал из UI + LLM не видел свои прошлые ответы.

**Причина:** Когда добавляешь поле к таблице — помним только про "главный" INSERT. Вторичные места (sister endpoints, background tasks, migration scripts, результаты воркера) теряем.

**Решение:** При изменении schema — обязательный grep:
```bash
grep -rn "INSERT INTO chat_messages" backend/
```
Для КАЖДОГО места пройтись и убедиться что новое поле передаётся. Добавить в checklist рефакторинга.

**Обнаружение:** Тестировали только user-flow (send → check history). Нужны integration-тесты на **полный цикл** (send → worker → results → check history). Этот тест добавлен: `test_assistant_response_preserves_session_id`.

**Теги:** #schema-migration #audit #integration-tests #silent-bug

## #16 [Process] Rev big ultrathink даёт новые находки даже на 3-м прогоне

**Проблема:** Провели Блок 1 + 2 + 3 (полный рев biг архитектуры, кода, тестов, frontend, ops). Казалось — закрыли всё. Дима попросил повторить. Второй прогон нашёл `results.py session_id` + `sass-embedded` (2 блокера). Третий прогон с РАЗНОЙ специализацией researcher-ов нашёл ещё **11 блокеров** (SSE session filter, /content endpoint, cold start, mode, delete_session guard, apiFetch timeout, canSend workerOnline, background_parse async, delete_project cascade и др.).

**Причина:** Каждый прогон видит проект под своим углом. Разная конфигурация специалистов → разные дыры:
- Прогон #1: архитектура + код + тесты + frontend + ops (широкий)
- Прогон #2: release-readiness (focus на готовность)
- Прогон #3: data-integrity + api-contracts + concurrency + user-flows + rust + error-paths (узко специализированные)

**Решение / принцип:** Для мажорного релиза — **2-3 прогона rev big с разной специализацией**. Не ждать "может сами поймаем" — каждый прогон стоит 1-2 часов agent-времени, но ловит баги которые пользователь увидел бы **в первый день** работы с релизом.

**Экономика:** третий прогон поймал 11 блокеров за ~15 минут wall-clock (6 agents параллельно). Без него v33 выпустили бы с 11 UX-багами = неделя исправлений post-release + потеря доверия пользователя.

**Теги:** #process #rev-big #pre-release #quality

## #17 [Release] `nr` не bumpит frontend/package.json и root package.json

**Проблема:** Скрипт `nr` (шорткат глобального CLAUDE.md) bumpит версию в 3 файлах: `src-tauri/Cargo.toml`, `src-tauri/tauri.conf.json`, `backend/core/config.py`. Для v33 этого оказалось недостаточно — `frontend/package.json` и корневой `package.json` остались на 1.0.0 (обнаружено в rev big #2).

**Решение:** Bump версии в **5 файлах + Cargo.lock**:
1. `src-tauri/Cargo.toml` — `version = "N.0.0"`
2. `src-tauri/tauri.conf.json` — `"version": "N.0.0"`
3. `backend/core/config.py` — `APP_VERSION: str = "N.0.0"`
4. `frontend/package.json` — `"version": "N.0.0"`
5. `package.json` (root) — `"version": "N.0.0"`
6. `src-tauri/Cargo.lock` — поле `version` у package `nastya-orchestrator`

**TODO:** обновить `nr` скрипт (в `~/.claude/shortcuts.md`) — добавить шаг 4-6. Пока — ручной bump после `nr`.

**Маркер проблемы:** после release commit `grep -E '"version"|APP_VERSION' src-tauri/Cargo.toml src-tauri/tauri.conf.json backend/core/config.py frontend/package.json package.json` — ВСЕ должны быть на одной версии.

**Теги:** #release #version-sync #nr-gap

## #18 [CI] ci.yml на Ubuntu НЕ ловит Windows-специфичные баги для Windows-only релиза

**Проблема:** Добавили `ci.yml` с `runs-on: ubuntu-latest` для pull_request/push (lint/test/build). Release workflow (`release.yml`) — Windows. CI зелёный → push тега → release падает на Windows 3 раза подряд.

**Суть:** для десктоп-приложения на Windows ловить баги только на Linux CI бесполезно в критичных зонах:
- **SQLite WAL:** на Linux прощающий, на Windows строже (урок #12)
- **Pathлиние сепараторы** (`\` vs `/`)
- **Encoding** (cp866 vs utf-8)
- **Subprocess/shell escape** различия
- **Зависимости** которые ставятся по-разному (pyinstaller, sass-embedded на Windows)

v33 стоил 3 hotfix-а (pytest-asyncio, busy_timeout, sync-wrappers) — все 3 воспроизвелись бы ДО release если бы CI был на Windows.

**Решение:** в `.github/workflows/ci.yml` добавить matrix:
```yaml
jobs:
  test-python:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
```

Linting (ruff/mypy/clippy/eslint) оставить на ubuntu — быстрее и OS-независимо. Но **pytest** + **build** — обязательно на matrix с Windows.

**Экономика:** +5 минут на CI run vs 1 час hotfix-chain + force-push тега + тревога. При частых релизах окупается с первого раза.

**Маркер:** тесты зелёные в CI, но красные при release на Windows. Значит OS-specific разница — добавь Windows в CI matrix.

**Теги:** #ci #windows #matrix #cross-platform #release-safety

## #19 [Release] Force-push тега безопасен когда CI упал до publish artifacts

**Проблема:** Релиз v33 упал 3 раза подряд на разных шагах. Тег `v33` уже pushed, но GitHub Release page пустая (release workflow fail-ed до "Publish GitHub Release" step). Выбор: (a) `git push --force origin v33` на новый commit, (b) bump до `v34`.

**Решение — force-push тега (a):** т.к. **никто не скачал broken v33** (artifacts не опубликованы). Семантика сохраняется: v33 = первый **рабочий** релиз. История без косметического мусора из failed v33.

**Когда НЕ делать force-push тега:**
- На странице `https://github.com/.../releases/tag/vN` есть `setup.exe` / `latest.json` — кто-то мог скачать → force-push создаст inconsistency с `latest.json`
- Тег старше 1 дня — возможны кэши у CI runners / mirrors / пользователей
- Tauri updater уже обнаружил `latest.json` и закачал у пользователей

**Чеклист перед force-push тега:**
1. Открой release page — пусто? ОК.
2. `curl -I https://github.com/.../releases/download/vN/latest.json` — 404? ОК.
3. Прошло <1 часа с push тега? ОК.

Если любой ❌ → bump до `vN+1` вместо force.

**Команды:**
```bash
git tag -f v33                    # local move
git push --force origin v33       # remote force move
```

**Теги:** #git #tags #force-push #release-safety #destructive-op

## #20 [Integration] TS-тайпы ≠ runtime API-контракт — frontend call-sites audit нужен после schema changes

**Проблема:** В v33 добавили `session_id` в `documents`. Backend upload endpoint принимает `session_id: str | None = None` — optional. Frontend `ChatPanel.tsx:237` при upload clipboard-картинки **НЕ передавал** session_id → все clipboard-документы сохранялись с `session_id=NULL` → становились project-wide → виделись LLM во всех чатах.

Баг пережил v33 → v34 (Quality Release) и пойман **только в v35** когда Дима руками протестировал UX: "модель видит две картинки" — **та же самая проблема** что чинили chat-сессиями изначально.

**Почему не поймали:**
- TypeScript молчал — query params в fetch URL не в строгой схеме, передача любых string-ов OK
- tsc зелёный, pytest зелёный (тестировал backend изолированно, не real flow)
- Rev big #3 отметил это как 🟡 риск по GET endpoint, но POST upload не проверил детально
- CHANGELOG-описание «изоляция картинок» создавало иллюзию что всё работает

**Уроки:**

1. **Integration-тест обязателен на critical UX flow.** Для chat-sessions нужен был e2e: upload clipboard → check БД что session_id прописался. Юнит-тесты backend в изоляции бесполезны для cross-boundary багов.

2. **Runtime-контракт сильнее типа-совместимости.** Если поле REQUIRED по смыслу (как session_id для is_scratch) — делай его required на бэкенде:
   ```python
   if is_scratch and not session_id:
       raise HTTPException(422, "session_id обязателен для clipboard-картинок")
   ```
   Тогда frontend упадёт сразу при разработке, а не в проде через 3 релиза.

3. **После schema changes — `grep endpoint-url` во ВСЕХ fronted файлах.** Не только audit backend (урок #15), но все call-sites на фронте. Каждый должен быть явно обновлён чтобы передавать новое обязательное поле.

4. **Dima's manual test > авто-тесты** для UX-сценариев. Даже 245 зелёных pytest не заменят "запусти приложение и попробуй приложить картинку". После мажорного релиза — обязательно dogfood session.

**Маркер проблемы:** фича задекларирована работающей, тесты зелёные, но реальный UX сценарий показывает регрессию. Значит integration-тест отсутствует.

**Теги:** #integration-testing #runtime-contract #frontend-audit #schema-migration #dogfooding

## Краткая справка по стеку (2026-04-08)

- **Frontend:** React 19 + Vite (TS)
- **Backend:** FastAPI + uvicorn + SQLite (WAL)
- **Worker:** Python, httpx async, subprocess для codex/claude CLI
- **Desktop:** Tauri 2, NSIS per-user installer, WebView2 bootstrapper
- **Бинари в бандле:** 3 sidecar — nastya-backend.exe (75 MB), nastya-worker.exe (70 MB), opera-proxy.exe (7.5 MB)
- **CI:** GitHub Actions windows-latest + cargo tauri build + подпись через minisign
- **Локальный билд:** `local-build.bat` через PortableBuildTools + Rust stable + tauri-cli 2.x
- **Signing:** ключи в `%USERPROFILE%\.tauri\nastya.key` (private) + pubkey в `tauri.conf.json`
