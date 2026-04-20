"""
Диагностика документов и чат-сессий для дебага бага 'модель видит две картинки'.

Запуск (Windows, из корня проекта):
    cd D:\\Share\\nastyaorc
    python scripts\\diag_documents.py

Запуск (WSL / Linux):
    cd /mnt/d/Share/nastyaorc
    python3 scripts/diag_documents.py

Путь к БД берётся из backend/core/config.py::DB_PATH.
Отправь вывод этого скрипта целиком — он покажет откуда LLM берёт лишние картинки.
"""
import sqlite3
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path чтобы импорт backend.core.config сработал
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.config import DB_PATH


def main() -> None:
    print(f"=== БД: {DB_PATH} ===\n")

    if not Path(DB_PATH).exists():
        print("ERROR: БД не найдена. Запусти приложение хотя бы раз.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # 1. Проекты
    # ------------------------------------------------------------------
    print("## Проекты")
    projects = conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
    if not projects:
        print("  (нет проектов)")
    for r in projects:
        print(f"  - {r['id'][:8]}  {r['name']!r}")

    # ------------------------------------------------------------------
    # 2. Сессии
    # ------------------------------------------------------------------
    print("\n## Сессии (последние 20, по убыванию updated_at)")
    sessions = conn.execute("""
        SELECT s.id, s.project_id, s.title, s.created_at, s.updated_at,
               (SELECT COUNT(*) FROM chat_messages WHERE session_id = s.id) AS msg_count
        FROM chat_sessions s
        ORDER BY s.updated_at DESC
        LIMIT 20
    """).fetchall()
    if not sessions:
        print("  (нет сессий)")
    for r in sessions:
        print(
            f"  - {r['id'][:8]}  proj={r['project_id'][:8]}  "
            f"msgs={r['msg_count']}  title={r['title']!r}  updated={r['updated_at']}"
        )

    # ------------------------------------------------------------------
    # 3. Документы — ГЛАВНОЕ
    # ------------------------------------------------------------------
    print("\n## Документы (всё что может попасть в LLM prompt)")
    docs = conn.execute("""
        SELECT d.id, d.project_id, d.filename, d.is_scratch, d.session_id,
               d.parse_status, d.size, d.created_at,
               s.title AS session_title
        FROM documents d
        LEFT JOIN chat_sessions s ON d.session_id = s.id
        ORDER BY d.project_id, d.created_at DESC
    """).fetchall()
    if not docs:
        print("  (нет документов)")
    for d in docs:
        scope   = "session"      if d["session_id"] else "project-wide"
        scratch = "scratch"      if d["is_scratch"]  else "normal"
        sid     = d["session_id"][:8] if d["session_id"] else "-"
        title   = d["session_title"] or "-"
        print(f"  - {d['id'][:8]}  filename={d['filename']!r}")
        print(f"      proj={d['project_id'][:8]}  {scope}/{scratch}  session={sid}  title={title!r}")
        print(f"      size={d['size']}  parse={d['parse_status']}  created={d['created_at']}")

    # ------------------------------------------------------------------
    # 4. Дубликаты по имени
    # ------------------------------------------------------------------
    print("\n## Дубликаты по имени (filename встречается > 1 раза)")
    dups = conn.execute("""
        SELECT filename, COUNT(*) AS cnt
        FROM documents
        GROUP BY filename
        HAVING cnt > 1
        ORDER BY cnt DESC
    """).fetchall()
    if not dups:
        print("  (дубликатов нет)")
    for r in dups:
        print(f"  - {r['filename']!r} × {r['cnt']}")

    # ------------------------------------------------------------------
    # 5. Все image-документы по проектам
    # ------------------------------------------------------------------
    print("\n## Все image-документы в каждом проекте")
    for p in projects:
        print(f"\n  Проект: {p['name']!r}  ({p['id'][:8]})")
        imgs = conn.execute("""
            SELECT filename, is_scratch, session_id, created_at
            FROM documents
            WHERE project_id = ?
              AND (
                    LOWER(filename) LIKE '%.png'
                 OR LOWER(filename) LIKE '%.jpg'
                 OR LOWER(filename) LIKE '%.jpeg'
                 OR LOWER(filename) LIKE '%.webp'
                 OR LOWER(filename) LIKE 'clipboard-%'
              )
            ORDER BY created_at DESC
        """, (p["id"],)).fetchall()
        if not imgs:
            print("    (нет картинок)")
        for i in imgs:
            sid = i["session_id"][:8] if i["session_id"] else "-"
            print(
                f"    - {i['filename']!r}  scratch={i['is_scratch']}  "
                f"session={sid}  {i['created_at']}"
            )

    # ------------------------------------------------------------------
    # 6. Индикатор миграции #12
    # ------------------------------------------------------------------
    print("\n## Индикатор миграции #12 (переименование orphan image.png)")
    orphans = conn.execute("""
        SELECT COUNT(*) FROM documents
        WHERE is_scratch = 1
          AND LOWER(filename) IN ('image.png', 'image.jpg', 'image.jpeg')
    """).fetchone()[0]
    status = "OK — миграция v36 сработала" if orphans == 0 else f"ВНИМАНИЕ: {orphans} устаревших (ожидается 0)"
    print(f"  orphan clipboards с устаревшим именем: {orphans}  [{status}]")

    # ------------------------------------------------------------------
    # 7. Последние 5 сообщений с картинками (для понимания что LLM получает)
    # ------------------------------------------------------------------
    print("\n## Последние сообщения содержащие 'image' в content (до 5)")
    try:
        msgs = conn.execute("""
            SELECT m.id, m.session_id, m.role, m.created_at,
                   SUBSTR(m.content, 1, 200) AS snippet
            FROM chat_messages m
            WHERE LOWER(m.content) LIKE '%image%'
               OR LOWER(m.content) LIKE '%clipboard%'
            ORDER BY m.created_at DESC
            LIMIT 5
        """).fetchall()
        if not msgs:
            print("  (нет сообщений с упоминанием image/clipboard)")
        for m in msgs:
            print(f"  - {m['id'][:8]}  session={m['session_id'][:8]}  role={m['role']}  {m['created_at']}")
            print(f"      snippet: {m['snippet']!r}")
    except Exception as e:
        print(f"  (таблица chat_messages недоступна: {e})")

    conn.close()
    print("\n=== Диагностика завершена ===")


if __name__ == "__main__":
    main()
