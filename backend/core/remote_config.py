"""
Remote config — подтягивание настроек из публичного GitHub репо при старте backend.

Архитектура:
    Дима правит remote-config.json в master репо → git push
    ↓
    backend.main lifespan startup → fetch_remote_config() (с retry)
    ↓
    httpx.get(https://raw.githubusercontent.com/.../remote-config.json)
    через opera-proxy (HTTPS_PROXY=127.0.0.1:18080 → Opera EU → GitHub)
    ↓
    Сохраняем в app.state.remote_config как dict
    ↓
    /api/system/remote-config → frontend.store.loadRemoteConfig()
    ↓
    StatusBar.tsx рендерит header_emoji рядом с версией

Зачем: не делать новый installer на каждое мелкое изменение (дефолт модели,
флаги фичей, смайлик в шапке, сообщения для пользователей). Изменения
применяются при следующем перезапуске приложения у пользователя.

Репо публичный — токен не нужен. Раньше использовался GITHUB_PAT из _secrets.py,
но это запекалось в frozen bundle и усложняло сборку (hiddenimports) + создавало
риск утечки токена через strings на .exe. Теперь fetch идёт без auth через
raw.githubusercontent.com — CDN GitHub, отдаёт статику мгновенно.

Retry: opera-proxy поднимается параллельно с backend на startup, может быть
не готов к моменту первого fetch → ConnectionRefused. Делаем 3 попытки
с backoff 1s/3s/7s, покрывая worst-case задержку прокси.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Raw CDN — публичный репо, без API rate limits (5000/ч у API против ∞ у raw)
REMOTE_CONFIG_URL = (
    "https://raw.githubusercontent.com/"
    "ativubise657-boop/nastyaorchestrator/master/remote-config.json"
)

# Таймаут одной попытки
FETCH_TIMEOUT = 8.0

# Retry: opera-proxy может не успеть подняться → ConnectionRefused на первой попытке.
# Backoff покрывает типичный старт прокси (до ~10 секунд суммарно).
_RETRY_DELAYS = (1.0, 3.0, 7.0)


def fetch_remote_config() -> dict[str, Any]:
    """
    Синхронный fetch remote-config из GitHub raw CDN. Возвращает dict.
    Никогда не бросает исключения — при ошибке возвращает пустой dict.

    Делает до 3 попыток с backoff — защита от race condition при startup
    когда opera-proxy (HTTPS_PROXY) ещё не забиндился на :18080.

    Вызывается из backend.main lifespan startup. Сеть идёт через opera-proxy
    (HTTPS_PROXY уже установлен proxy_module.apply_to_env, trust_env=True).
    """
    headers = {
        "User-Agent": "nastyaorchestrator-backend",
        "Cache-Control": "no-cache",
    }

    last_exc: Exception | None = None
    for attempt, delay in enumerate([0.0, *_RETRY_DELAYS], start=1):
        if delay:
            time.sleep(delay)
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT, trust_env=True) as client:
                r = client.get(REMOTE_CONFIG_URL, headers=headers)

            if r.status_code != 200:
                logger.warning(
                    "Remote config: HTTP %d (попытка %d) — %s",
                    r.status_code, attempt,
                    r.text[:200].replace("\n", " "),
                )
                # HTTP-ошибки (404/5xx) обычно не лечатся retry — выходим сразу
                return {}

            data = json.loads(r.text)
            if not isinstance(data, dict):
                logger.warning("Remote config: ожидали dict, получили %s", type(data).__name__)
                return {}

            logger.info(
                "Remote config загружен (version=%s, keys=%s, попытка %d)",
                data.get("version", "?"),
                list(data.keys()),
                attempt,
            )
            return data

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            # Транзиентные — прокси не готов или сеть моргнула → retry
            last_exc = exc
            logger.info(
                "Remote config: %s (попытка %d/%d) — retry через %ss",
                type(exc).__name__, attempt, len(_RETRY_DELAYS) + 1,
                _RETRY_DELAYS[attempt - 1] if attempt <= len(_RETRY_DELAYS) else "—",
            )
            continue
        except Exception as exc:
            # Непредвиденные — не retry, сразу наружу как {}
            logger.warning("Remote config: %s: %s", type(exc).__name__, exc)
            return {}

    logger.warning(
        "Remote config: все %d попыток провалились, последняя ошибка: %s",
        len(_RETRY_DELAYS) + 1, last_exc,
    )
    return {}
