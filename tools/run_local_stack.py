from __future__ import annotations

import argparse
import locale
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STREAM_ENCODING = locale.getpreferredencoding(False) or "utf-8"


def configure_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def prefixed_stream(name: str, process: subprocess.Popen[str]) -> None:
    if process.stdout is None:
        return

    for line in iter(process.stdout.readline, ""):
        text = line.rstrip()
        if text:
            print(f"[{name}] {text}")


def start_process(name: str, args: list[str], env: dict[str, str]) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        args,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding=STREAM_ENCODING,
        errors="replace",
        bufsize=1,
    )
    threading.Thread(target=prefixed_stream, args=(name, process), daemon=True).start()
    return process


def wait_for_health(port: int, timeout: int) -> bool:
    url = f"http://127.0.0.1:{port}/api/system/health"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.5)
    return False


def stop_process(name: str, process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return

    print(f"[stack] stopping {name}...")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    configure_console()

    parser = argparse.ArgumentParser(description="Run Nastya backend and worker in one console.")
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--health-timeout", type=int, default=20)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    backend: subprocess.Popen[str] | None = None
    worker: subprocess.Popen[str] | None = None

    try:
        print("[stack] starting backend...")
        backend = start_process(
            "backend",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--workers",
                "1",
                "--log-level",
                "info",
            ],
            env,
        )

        if not wait_for_health(args.port, args.health_timeout):
            print(f"[stack] backend did not become healthy on port {args.port}")
            return 1

        print("[stack] backend is healthy")
        print("[stack] starting worker...")
        worker = start_process("worker", [sys.executable, "-m", "worker.main"], env)
        time.sleep(1.5)

        if worker.poll() is not None:
            print(f"[stack] worker exited early with code {worker.returncode}")
            return 1

        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")

        if args.smoke_test:
            print("[stack] smoke test passed")
            return 0

        print("[stack] backend + worker are running. Press Ctrl+C to stop.")

        while True:
            if backend.poll() is not None:
                print(f"[stack] backend exited with code {backend.returncode}")
                return backend.returncode or 1
            if worker.poll() is not None:
                print(f"[stack] worker exited with code {worker.returncode}")
                return worker.returncode or 1
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[stack] shutdown requested")
        return 0
    finally:
        stop_process("worker", worker)
        stop_process("backend", backend)


if __name__ == "__main__":
    raise SystemExit(main())
