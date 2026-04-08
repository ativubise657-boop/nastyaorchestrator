# Сборка standalone .exe (PyInstaller)

Собирает `backend` и `worker` в два самостоятельных Windows-исполняемых файла.
Они потом подкладываются как **sidecar'ы** в Tauri-обёртку.

## Что получается

| Артефакт | Что внутри | Размер (примерно) |
|---|---|---|
| `dist/nastya-backend.exe` | FastAPI + uvicorn + markitdown + все конвертеры | ~80–120 МБ |
| `dist/nastya-worker.exe` | httpx + markitdown + sqlite | ~70–100 МБ |

Оба бинаря — **onefile, console**. Логи видны в окне (Tauri sidecar их перехватывает).

## Где должны лежать runtime-данные

`.exe` ожидает, что **рядом с ним** будут:

```
nastya-backend.exe
nastya-worker.exe
.env                      (опционально — переменные окружения)
data/
  nastya.db
  documents/
frontend/
  dist/                   (опционально — если есть, backend сам раздаст статику)
```

`runtime_hook.py` при запуске:
- делает `chdir` в директорию `.exe`,
- если видит `frontend/dist/` — выставляет `SERVE_STATIC=true`,
- подгружает `.env` рядом с `.exe`.

`backend/core/config.py` имеет fallback: при `sys.frozen` берёт `BASE_DIR = Path(sys.executable).parent`, иначе — обычная корневая директория репо.

## Сборка локально (только Windows)

> На WSL/Linux собрать **нельзя** — PyInstaller делает платформенно-зависимый бинарь.

```cmd
cd nastyaorc
build\build.bat
```

Скрипт создаст `.venv-build`, поставит `requirements.txt` (там уже есть `pyinstaller`) и прогонит оба spec-файла. Артефакты — в `dist/`.

## Сборка на CI (GitHub Actions, windows-latest)

Минимальный шаг:

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"

- run: pip install -r requirements.txt

- run: pyinstaller --noconfirm --clean build/backend.spec
- run: pyinstaller --noconfirm --clean build/worker.spec

- uses: actions/upload-artifact@v4
  with:
    name: nastya-exes
    path: dist/*.exe
```

## Иконка

`build/icon.ico` пока **не существует** — в spec-файлах прописан fallback на `None`.
Когда появится — просто положить файл, ничего править не нужно.

## Что НЕ вшивается в .exe

- `data/` — runtime, лежит рядом
- `documents/` — runtime, лежит рядом
- `frontend/dist/` — раздаётся опционально через `SERVE_STATIC`, не вшивается
- `.env` — лежит рядом

## Hidden imports — на что обратить внимание на CI

PyInstaller плохо находит динамически импортируемые модули. Если на CI получишь `ModuleNotFoundError` при запуске `.exe`, скорее всего нужно дописать имя в `hiddenimports` соответствующего spec-файла. Подозрительные кандидаты:

- **uvicorn loops/protocols** — уже перечислены, но если поменяется версия uvicorn, имена могут сдвинуться
- **markitdown converters** — закрыто через `collect_submodules("markitdown")`, но если markitdown поменяет внутреннюю структуру (мы фиксируем 0.1.5), может потребоваться ручной список
- **magika** — модели для определения типа файла лежат в data-files пакета. Если markitdown не сможет определять типы — добавить `collect_data_files("magika")` (уже есть)
- **pdfminer cmap-таблицы** — `collect_data_files("pdfminer")` обязателен, иначе PDF будут парситься без шрифтов
- **lxml** — `lxml._elementpath` часто теряется, явно прописан
- **openpyxl.cell._writer** — динамический импорт, явно прописан

## Тест после сборки

```cmd
cd dist
mkdir data
nastya-backend.exe
```

Должен подняться на `http://localhost:8781/docs`. Если нет — смотри traceback в окне.

Worker запускается отдельно (нужен `WORKER_TOKEN` в env или `.env`):

```cmd
set WORKER_TOKEN=change-me
nastya-worker.exe
```
