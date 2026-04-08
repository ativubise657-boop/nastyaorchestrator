"""
Единый прокси-слой Nastya Orchestrator.

Принцип:
- Дефолтные креды захардкожены (у Насти Win10 без админа, наружу — только через корп-прокси).
- Override в БД (таблица app_settings, ключи proxy_*).
- apply_to_env() выставляет HTTPS_PROXY/HTTP_PROXY/NO_PROXY в os.environ → это
  автоматически наследуют:
    · все subprocess (git, pip, npm, codex, claude) — git и pip читают эти env;
    · httpx.AsyncClient (по умолчанию trust_env=True);
    · requests / urllib (тоже читают HTTPS_PROXY).
  Дополнительно ничего модифицировать не требуется.
- Хранение пароля — открытым текстом (явное решение, см. precompact-15132).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Дефолтные креды (Дима выдал — у Насти на Win10 нужны для всех исходящих).
DEFAULT_PROXY_HOST = "94.103.191.13"
DEFAULT_PROXY_PORT = 3528
DEFAULT_PROXY_USER = "user393678"
DEFAULT_PROXY_PASS = "a6g7ln"
DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1"
DEFAULT_PROXY_ENABLED = True

# Ключи в таблице app_settings
KEY_ENABLED = "proxy_enabled"
KEY_HOST = "proxy_host"
KEY_PORT = "proxy_port"
KEY_USER = "proxy_user"
KEY_PASS = "proxy_pass"
KEY_NO_PROXY = "proxy_no_proxy"


@dataclass
class ProxySettings:
    enabled: bool
    host: str
    port: int
    user: str
    password: str
    no_proxy: str

    def to_url(self) -> str:
        """Сборка URL вида http://user:pass@host:port (без схемы https — у нас HTTP-прокси)."""
        if self.user:
            return f"http://{self.user}:{self.password}@{self.host}:{self.port}"
        return f"http://{self.host}:{self.port}"

    def to_safe_dict(self) -> dict:
        """Для логов/UI — без пароля."""
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "no_proxy": self.no_proxy,
        }


def default_settings() -> ProxySettings:
    return ProxySettings(
        enabled=DEFAULT_PROXY_ENABLED,
        host=DEFAULT_PROXY_HOST,
        port=DEFAULT_PROXY_PORT,
        user=DEFAULT_PROXY_USER,
        password=DEFAULT_PROXY_PASS,
        no_proxy=DEFAULT_NO_PROXY,
    )


def _read_kv(state, key: str) -> Optional[str]:
    row = state.fetchone("SELECT value FROM app_settings WHERE key = ?", (key,))
    return row["value"] if row else None


def _write_kv(state, key: str, value: str) -> None:
    state.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def load_settings(state) -> ProxySettings:
    """Читает настройки из БД, fallback на дефолты для отсутствующих ключей."""
    d = default_settings()
    try:
        enabled_raw = _read_kv(state, KEY_ENABLED)
        host = _read_kv(state, KEY_HOST) or d.host
        port_raw = _read_kv(state, KEY_PORT)
        user = _read_kv(state, KEY_USER)
        if user is None:
            user = d.user
        password = _read_kv(state, KEY_PASS)
        if password is None:
            password = d.password
        no_proxy = _read_kv(state, KEY_NO_PROXY) or d.no_proxy

        enabled = (enabled_raw or ("1" if d.enabled else "0")).lower() in ("1", "true", "yes")
        try:
            port = int(port_raw) if port_raw else d.port
        except ValueError:
            port = d.port

        return ProxySettings(
            enabled=enabled,
            host=host,
            port=port,
            user=user,
            password=password,
            no_proxy=no_proxy,
        )
    except Exception as exc:
        logger.warning("Не удалось прочитать proxy-настройки из БД: %s — использую дефолты", exc)
        return d


def save_settings(state, settings: ProxySettings) -> None:
    _write_kv(state, KEY_ENABLED, "1" if settings.enabled else "0")
    _write_kv(state, KEY_HOST, settings.host)
    _write_kv(state, KEY_PORT, str(settings.port))
    _write_kv(state, KEY_USER, settings.user)
    _write_kv(state, KEY_PASS, settings.password)
    _write_kv(state, KEY_NO_PROXY, settings.no_proxy)
    state.commit()


def apply_to_env(settings: ProxySettings) -> None:
    """
    Выставляет HTTPS_PROXY/HTTP_PROXY/NO_PROXY (и lower-case дубли) в os.environ.
    Если enabled=False — снимает их.
    """
    upper_lower_pairs = [
        ("HTTPS_PROXY", "https_proxy"),
        ("HTTP_PROXY", "http_proxy"),
        ("ALL_PROXY", "all_proxy"),
        ("NO_PROXY", "no_proxy"),
    ]
    if not settings.enabled:
        for u, l in upper_lower_pairs:
            os.environ.pop(u, None)
            os.environ.pop(l, None)
        logger.info("Прокси отключён — env-переменные очищены")
        return

    url = settings.to_url()
    os.environ["HTTPS_PROXY"] = url
    os.environ["https_proxy"] = url
    os.environ["HTTP_PROXY"] = url
    os.environ["http_proxy"] = url
    os.environ["ALL_PROXY"] = url
    os.environ["all_proxy"] = url
    os.environ["NO_PROXY"] = settings.no_proxy
    os.environ["no_proxy"] = settings.no_proxy
    logger.info("Прокси применён в env: %s:%s (no_proxy=%s)", settings.host, settings.port, settings.no_proxy)


def apply_from_db(state) -> ProxySettings:
    """Шорткат: загрузить из БД и применить. Возвращает применённые настройки."""
    s = load_settings(state)
    apply_to_env(s)
    return s


def test_proxy(settings: ProxySettings, timeout: float = 8.0) -> tuple[bool, str]:
    """
    Проверочный запрос через переданные настройки прокси.
    НЕ модифицирует os.environ — использует явный httpx-клиент.
    """
    import httpx

    url = settings.to_url() if settings.enabled else None
    try:
        with httpx.Client(proxy=url, timeout=timeout, trust_env=False) as client:
            r = client.get("https://api.github.com/zen")
            if r.status_code == 200:
                return True, f"OK: {r.text.strip()[:80]}"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
