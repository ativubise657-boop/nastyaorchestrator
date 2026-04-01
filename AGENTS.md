# AGENTS.md

> **Глобальные инструкции:** `~/.Codex/AGENTS.md` (персона Клодик, режимы ag/agr/rev, принципы кода, шорткаты)

## Project

**Nastya Orchestrator** — оркестратор для управления проектами Насти. Чат-интерфейс → Codex CLI выполняет.

| Param | Value |
|-------|-------|
| Stack | FastAPI (Python 3.12) + React 19 + TypeScript 5 + Zustand 5 + Vite 6 |
| DB | SQLite WAL mode (`data/nastya.db`, gitignored) |
| Worker | Codex CLI adapter, HTTP polling, async |
| Output streams | SSE (task_chunk, task_update, worker_status) |
| Domain | nr.gnld.ru |
| Server | 185.93.111.88 (`/opt/nastya-orch/`) |
| Port | 8781 |
| Color scheme | #722e85 (фиолетовый) |

## Build & Run

```bash
# Backend
source .venv/bin/activate
uvicorn backend.main:app --host 127.0.0.1 --port 8781 --reload

# Frontend
cd frontend && npm run dev -- --port 5176 --host

# Worker
ORCH_SERVER_URL=http://localhost:8781 WORKER_TOKEN=test123 python -m worker.main

# Полный перезапуск
pkill -f "worker.main"; lsof -ti:8781 | xargs -r kill -9; lsof -ti:5176 | xargs -r kill -9
# Затем запустить все три
```

## Architecture

```
nastyaorchestrator/
├── backend/
│   ├── api/
│   │   ├── chat.py          ← POST /api/chat/send, GET /api/chat/history
│   │   ├── projects.py      ← CRUD проектов (без git)
│   │   ├── results.py       ← Приём результатов от worker
│   │   ├── documents.py     ← Загрузка/просмотр документов
│   │   ├── webhooks.py      ← Б24 вебхуки (заготовка)
│   │   └── system.py        ← Health, SSE stream, queue endpoints
│   ├── core/
│   │   ├── state.py         ← SQLite WAL: projects, tasks, chat, documents
│   │   ├── config.py        ← Env-переменные
│   │   ├── queue.py         ← Очередь задач (atomic dequeue)
│   │   └── auth.py          ← Bearer token для worker
│   ├── models/__init__.py   ← Pydantic модели
│   └── main.py              ← FastAPI app, lifespan, SSE pub/sub
├── worker/
│   ├── poller.py            ← Основной цикл: poll → execute → push
│   ├── executor.py          ← Codex CLI adapter (--print --verbose --output-format stream-json)
│   ├── result_pusher.py     ← HTTP клиент для отправки результатов
│   ├── mode_resolver.py     ← Автоопределение ag+/rev/solo
│   └── config.py            ← Env-переменные worker
├── frontend/src/
│   ├── stores/index.ts      ← Zustand (useShallow для React 19!)
│   ├── hooks/useSSE.ts      ← SSE: task_chunk, task_update, worker_status
│   ├── components/
│   │   ├── ChatPanel.tsx     ← Чат с аватарками, стриминг, модель
│   │   ├── Sidebar.tsx       ← Проекты + документы
│   │   └── StatusBar.tsx     ← Worker online/offline
│   └── App.tsx
├── config/
│   ├── projects.json         ← Seed-проекты
│   └── settings.json         ← Настройки
├── deploy/
│   ├── nginx.conf            ← nr.gnld.ru (listen 443 БЕЗ IP!)
│   ├── nastya-orchestrator.service
│   ├── deploy.sh             ← scp + restart
│   └── setup.sh              ← Первоначальная настройка сервера
└── data/                     ← Runtime (gitignored)
```

## Gotchas

- **Zustand + React 19:** селекторы с объектами вызывают infinite loop. Использовать `useShallow` или примитивные селекторы
- **Vite HMR:** при структурных изменениях (новые импорты, переименования) — удалить `node_modules/.vite/` и перезапустить
- **Codex CLI:** `--output-format stream-json` требует `--verbose`. `--bare` требует API ключ
- **SSE события:** `task_chunk` (не `result_chunk`!) для стриминга чанков
- **nginx 443:** ВСЕГДА generic `listen 443 ssl http2` без IP

## Lessons Learned

Файл: `memory/lessons-learned.md`
