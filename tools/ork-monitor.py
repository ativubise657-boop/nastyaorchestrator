#!/usr/bin/env python3
"""
Монитор оркестратора Насти — подключается к SSE и показывает ответы в реальном времени.
Запуск: python tools/ork-monitor.py [--url http://localhost:8781]

Показывает:
  - Стриминг ответов (task_chunk) по мере генерации
  - Завершение задач (task_update) с результатом
  - Фазы выполнения (task_phase)
  - Статус worker'а (worker_status)
"""

import argparse
import json
import sys
import time
from datetime import datetime

import httpx


# Цвета для терминала
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    GRAY = "\033[90m"


def ts():
    return datetime.now().strftime("%H:%M:%S")


def print_header():
    print(f"\n{C.BOLD}{C.CYAN}╔══════════════════════════════════════════╗{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}║   Монитор оркестратора Насти              ║{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}╚══════════════════════════════════════════╝{C.RESET}")
    print(f"{C.DIM}Ctrl+C для выхода{C.RESET}\n")


def format_project_name(project_id: str, projects: dict) -> str:
    return projects.get(project_id, project_id[:8])


def load_projects(base_url: str) -> dict:
    """Загрузить список проектов для отображения имён."""
    try:
        r = httpx.get(f"{base_url}/api/projects", timeout=5)
        return {p["id"]: p["name"] for p in r.json()}
    except Exception:
        return {}


def monitor(base_url: str):
    print_header()
    projects = load_projects(base_url)
    if projects:
        print(f"{C.DIM}Проекты: {', '.join(projects.values())}{C.RESET}\n")

    # Текущая стримящаяся задача
    streaming_task = None
    chunk_buffer = ""

    print(f"{C.GREEN}[{ts()}] Подключаюсь к {base_url}/api/events/stream ...{C.RESET}")

    while True:
        try:
            with httpx.stream("GET", f"{base_url}/api/events/stream", timeout=None) as response:
                print(f"{C.GREEN}[{ts()}] ✓ Подключено к SSE{C.RESET}\n")

                buf = ""
                for chunk in response.iter_text():
                    buf += chunk
                    # SSE-события разделены двойным переносом строки
                    while "\n\n" in buf:
                        raw_event, buf = buf.split("\n\n", 1)
                        event_type = None
                        event_data = None

                        for line in raw_event.strip().split("\n"):
                            if line.startswith("event: "):
                                event_type = line[7:]
                            elif line.startswith("data: "):
                                event_data = line[6:]
                            elif line.startswith(": "):
                                # Комментарий/keepalive — игнорируем
                                pass

                        if not event_type or not event_data:
                            continue

                        try:
                            data = json.loads(event_data)
                        except json.JSONDecodeError:
                            continue

                        # === task_chunk — стриминг ответа ===
                        if event_type == "task_chunk":
                            task_id = data.get("task_id", "?")
                            chunk_text = data.get("chunk", "")
                            proj = format_project_name(data.get("project_id", ""), projects)

                            if streaming_task != task_id:
                                # Новая задача — печатаем заголовок
                                streaming_task = task_id
                                chunk_buffer = ""
                                print(f"\n{C.BOLD}{C.BLUE}━━━ [{ts()}] Ответ ({proj}) ━━━{C.RESET}")

                            sys.stdout.write(chunk_text)
                            sys.stdout.flush()
                            chunk_buffer += chunk_text

                        # === task_update — статус задачи ===
                        elif event_type == "task_update":
                            task_id = data.get("task_id", "?")
                            status = data.get("status", "?")
                            proj = format_project_name(data.get("project_id", ""), projects)
                            result = data.get("result", "")
                            error = data.get("error", "")

                            if status == "running":
                                print(f"\n{C.YELLOW}[{ts()}] ⏳ Задача запущена ({proj}){C.RESET}")
                            elif status == "completed":
                                if streaming_task == task_id:
                                    # Уже стримили — просто закрываем
                                    print(f"\n{C.GREEN}━━━ [{ts()}] ✅ Завершено ({proj}) ━━━{C.RESET}\n")
                                else:
                                    # Не стримили — показать результат целиком
                                    print(f"\n{C.GREEN}[{ts()}] ✅ Ответ ({proj}):{C.RESET}")
                                    if result:
                                        # Обрезаем если слишком длинный
                                        display = result if len(result) < 2000 else result[:2000] + f"\n{C.DIM}... (обрезано, всего {len(result)} символов){C.RESET}"
                                        print(display)
                                    print()
                                streaming_task = None
                                chunk_buffer = ""
                            elif status == "failed":
                                print(f"\n{C.RED}[{ts()}] ❌ Ошибка ({proj}): {error}{C.RESET}\n")
                                streaming_task = None
                            elif status == "cancelled":
                                print(f"\n{C.YELLOW}[{ts()}] ⏹ Отменено ({proj}){C.RESET}\n")
                                streaming_task = None

                        # === task_phase — фаза выполнения ===
                        elif event_type == "task_phase":
                            phase = data.get("phase", "")
                            if phase:
                                print(f"{C.MAGENTA}[{ts()}] 📡 {phase}{C.RESET}")

                        # === worker_status ===
                        elif event_type == "worker_status":
                            online = data.get("online", False)
                            # Показываем только изменения (не спамим)
                            pass

        except httpx.ConnectError:
            print(f"{C.RED}[{ts()}] ✗ Не удалось подключиться к {base_url}{C.RESET}")
            print(f"{C.DIM}    Переподключение через 5 сек...{C.RESET}")
            time.sleep(5)
        except httpx.ReadTimeout:
            print(f"{C.YELLOW}[{ts()}] Таймаут чтения, переподключаюсь...{C.RESET}")
            time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n{C.DIM}Монитор остановлен{C.RESET}")
            sys.exit(0)
        except Exception as e:
            print(f"{C.RED}[{ts()}] Ошибка: {e}{C.RESET}")
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description="Монитор оркестратора Насти")
    parser.add_argument("--url", default="http://localhost:8781", help="URL бэкенда")
    args = parser.parse_args()
    monitor(args.url)


if __name__ == "__main__":
    main()
