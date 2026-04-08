# GitHub Actions Workflows

## release.yml — Windows Release Build

Собирает полный Windows-релиз: PyInstaller backend.exe + worker.exe, встраивает их как Tauri sidecars, билдит frontend (React 19) и упаковывает всё в NSIS installer через Tauri 2.

### Триггеры

| Триггер | Что делает |
|---------|------------|
| `push` тега `v*` (напр. `v1.0.0`) | Полный билд → артефакты → публикация GitHub Release с NSIS installer + raw exe + SHA256SUMS |
| `workflow_dispatch` (ручной запуск) | Полный билд → артефакты. Релиз НЕ публикуется (билд только для проверки) |

### Артефакты

Всегда загружаются в Actions run (`actions/upload-artifact@v4`, имя `nastya-windows-release`, 14 дней):

- `nastya-backend.exe` — raw PyInstaller backend
- `nastya-worker.exe` — raw PyInstaller worker
- `*.exe` — NSIS installer из `src-tauri/target/release/bundle/nsis/`
- `SHA256SUMS.txt` — контрольные суммы

При push тега те же файлы прикрепляются к GitHub Release.

### Как сделать релиз

```bash
git tag v1.0.0
git push origin v1.0.0
```

Workflow автоматически соберёт и опубликует release с auto-generated release notes.

Для ручной проверки сборки без публикации — запустить workflow через UI (вкладка Actions → Release Windows Build → Run workflow).

### Secrets (нужно настроить руками)

| Secret | Обязательность | Описание |
|--------|----------------|----------|
| `TAURI_PRIVATE_KEY` | Опционально | Приватный ключ Tauri updater (генерируется `cargo tauri signer generate`). Без него билд проходит, но installer не подписан для auto-update |
| `TAURI_KEY_PASSWORD` | Опционально | Пароль к приватному ключу Tauri |
| `GITHUB_TOKEN` | Автоматически | Выдаётся GitHub Actions, используется `softprops/action-gh-release` для публикации |

Signing включается автоматически если `TAURI_PRIVATE_KEY` задан. Без secret workflow делает unsigned-билд (первый релиз можно без подписи).

### TODO — что настроить Диме руками

1. **Secrets в репо** (Settings → Secrets and variables → Actions):
   - `TAURI_PRIVATE_KEY` — когда будет готов updater (сгенерировать через `cargo tauri signer generate -w ~/.tauri/nastya.key`)
   - `TAURI_KEY_PASSWORD` — пароль от ключа
2. **Permissions репо** (Settings → Actions → General → Workflow permissions): включить "Read and write permissions" — нужно для создания релизов
3. **Billing** — если репо приватный: проверить лимиты Actions minutes. Windows runners расходуют минуты в 2x. Публичные репо — бесплатно
4. **Branch protection** — при желании защитить `main` и разрешать push тегов только мейнтейнерам
5. **Первый релиз** — можно без `TAURI_PRIVATE_KEY`. Когда решим делать auto-update, сгенерировать ключ и добавить secrets. Публичный ключ прописать в `src-tauri/tauri.conf.json` (`plugins.updater.pubkey`)

### Стек билда (справочно)

- Python 3.12 + `requirements.txt` (pyinstaller 6.11.1, markitdown 0.1.5)
- Node 20 + `frontend/package-lock.json`
- Rust stable (MSVC toolchain из коробки на `windows-latest`)
- `tauri-cli ^2.0` через `cargo install --locked`
- Кеш: pip (setup-python), npm (setup-node), cargo registry + target (actions/cache)
