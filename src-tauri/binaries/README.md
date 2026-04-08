# Sidecar binaries

Tauri ожидает в этой папке собранные через PyInstaller бинари backend и worker
с **обязательным** platform-suffix в имени. Без суффикса `tauri build` упадёт
с ошибкой "binary not found".

## Целевые имена (Windows x64)

```
binaries/
  nastya-backend-x86_64-pc-windows-msvc.exe
  nastya-worker-x86_64-pc-windows-msvc.exe
```

В `tauri.conf.json` они прописаны без суффикса:
```json
"externalBin": [
  "binaries/nastya-backend",
  "binaries/nastya-worker"
]
```

Tauri сам подставит `-x86_64-pc-windows-msvc.exe` под текущий target triple.

## Как сюда попадают файлы

Фаза 2 (PyInstaller) собирает:
- `build/dist/nastya-backend.exe`
- `build/dist/nastya-worker.exe`

Перед `tauri build` (в GitHub Actions windows-latest) копируем их с переименованием:

```powershell
# .github/workflows/build.yml шаг "prepare sidecars"
Copy-Item build/dist/nastya-backend.exe `
  src-tauri/binaries/nastya-backend-x86_64-pc-windows-msvc.exe
Copy-Item build/dist/nastya-worker.exe `
  src-tauri/binaries/nastya-worker-x86_64-pc-windows-msvc.exe
```

## Как узнать свой target triple

```bash
rustc -Vv | grep host
# host: x86_64-pc-windows-msvc
```

Для других платформ суффиксы будут другие (`aarch64-apple-darwin` и т.д.), но
мы билдим только под Win64 — фиксированно `x86_64-pc-windows-msvc`.

## Поведение в рантайме

- Backend стартует автоматически при запуске .exe (FastAPI поднимает `:8781`)
- Worker стартует параллельно
- При закрытии окна процессы **продолжают жить** (close-to-tray)
- Quit из tray — убивает оба sidecar и выходит
- Restart Backend из tray — kill + повторный spawn
