"""Клиент Bitrix24 REST API через исходящий webhook.

Поддерживает поиск и просмотр компаний и контактов.
Webhook URL вида: https://portal.bitrix24.ru/rest/USER_ID/TOKEN/
"""
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# URL webhook из переменной окружения
BITRIX_WEBHOOK_URL: str = os.getenv("BITRIX_WEBHOOK_URL", "")

# Таймаут запросов к Bitrix24
_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Ключевые слова для определения CRM-запросов
# ---------------------------------------------------------------------------

_CRM_KEYWORDS = [
    # Компании
    r"\bкомпани",
    r"\bфирм",
    r"\bорганизаци",
    r"\bюрлиц",
    r"\bюр\.?\s*лиц",
    # Клиенты / контакты
    r"\bклиент",
    r"\bзаказчик",
    r"\bконтакт",
    r"\bпокупател",
    r"\bпартнёр",
    r"\bпартнер",
    # Сделки / CRM
    r"\bсделк",
    r"\bcrm\b",
    r"\bб24\b",
    r"\bbitrix",
    # Типичные запросы по базе
    r"\bнайд[иё]",
    r"\bпокаж[иы]",
    r"\bспис[оа]к",
    r"\bвс[её]\s+клиент",
    r"\bвс[её]\s+компани",
]


def is_crm_query(prompt: str) -> bool:
    """Определяет, является ли вопрос запросом к CRM."""
    if not BITRIX_WEBHOOK_URL:
        return False
    text = prompt.lower()
    return any(re.search(pattern, text) for pattern in _CRM_KEYWORDS)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _url(method: str) -> str:
    """Строит полный URL для вызова метода Bitrix24."""
    base = BITRIX_WEBHOOK_URL.rstrip("/")
    return f"{base}/{method}"


def _format_company(c: dict) -> str:
    """Форматирует компанию для включения в контекст."""
    parts = [f"**{c.get('TITLE', '—')}** (ID: {c.get('ID', '?')})"]
    if c.get("PHONE"):
        phones = [p.get("VALUE", "") for p in c["PHONE"] if p.get("VALUE")]
        if phones:
            parts.append(f"  Телефон: {', '.join(phones)}")
    if c.get("EMAIL"):
        emails = [e.get("VALUE", "") for e in c["EMAIL"] if e.get("VALUE")]
        if emails:
            parts.append(f"  Email: {', '.join(emails)}")
    if c.get("DATE_CREATE"):
        parts.append(f"  Создана: {c['DATE_CREATE'][:10]}")
    if c.get("DATE_MODIFY"):
        parts.append(f"  Изменена: {c['DATE_MODIFY'][:10]}")
    if c.get("COMMENTS"):
        parts.append(f"  Примечание: {c['COMMENTS'][:200]}")
    return "\n".join(parts)


def _format_contact(c: dict) -> str:
    """Форматирует контакт для включения в контекст."""
    name_parts = filter(None, [c.get("LAST_NAME"), c.get("NAME"), c.get("SECOND_NAME")])
    full_name = " ".join(name_parts) or "—"
    parts = [f"**{full_name}** (ID: {c.get('ID', '?')})"]
    if c.get("POST"):
        parts.append(f"  Должность: {c['POST']}")
    if c.get("PHONE"):
        phones = [p.get("VALUE", "") for p in c["PHONE"] if p.get("VALUE")]
        if phones:
            parts.append(f"  Телефон: {', '.join(phones)}")
    if c.get("EMAIL"):
        emails = [e.get("VALUE", "") for e in c["EMAIL"] if e.get("VALUE")]
        if emails:
            parts.append(f"  Email: {', '.join(emails)}")
    if c.get("COMPANY_ID"):
        parts.append(f"  Компания ID: {c['COMPANY_ID']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Запросы к Bitrix24
# ---------------------------------------------------------------------------

async def search_companies(query: str, limit: int = 10) -> list[dict]:
    """Ищет компании по названию."""
    if not BITRIX_WEBHOOK_URL:
        return []
    params = {
        "FILTER[%TITLE]": query,
        "SELECT[]": ["ID", "TITLE", "PHONE", "EMAIL", "COMMENTS", "ASSIGNED_BY_ID", "DATE_CREATE", "DATE_MODIFY"],
        "ORDER[TITLE]": "ASC",
        "start": 0,
    }
    # Добавляем лимит через параметр навигации
    params["LIMIT"] = limit
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_url("crm.company.list"), params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
    except Exception as e:
        logger.warning("Ошибка поиска компаний в Б24: %s", e)
        return []


async def list_companies(limit: int = 20) -> list[dict]:
    """Получает список всех компаний (последние добавленные)."""
    if not BITRIX_WEBHOOK_URL:
        return []
    params = {
        "SELECT[]": ["ID", "TITLE", "PHONE", "EMAIL", "COMMENTS", "DATE_CREATE", "DATE_MODIFY"],
        "ORDER[DATE_CREATE]": "DESC",
        "LIMIT": limit,
        "start": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_url("crm.company.list"), params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
    except Exception as e:
        logger.warning("Ошибка получения компаний из Б24: %s", e)
        return []


async def search_contacts(query: str, limit: int = 10) -> list[dict]:
    """Ищет контакты по имени/фамилии."""
    if not BITRIX_WEBHOOK_URL:
        return []
    # Поиск одновременно по имени и фамилии
    results: list[dict] = []
    for field in ["%NAME", "%LAST_NAME"]:
        params = {
            f"FILTER[{field}]": query,
            "SELECT[]": ["ID", "NAME", "LAST_NAME", "SECOND_NAME", "POST",
                         "PHONE", "EMAIL", "COMPANY_ID"],
            "ORDER[LAST_NAME]": "ASC",
            "LIMIT": limit,
            "start": 0,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_url("crm.contact.list"), params=params)
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("result", []):
                    # Дедуплицируем по ID
                    if not any(r["ID"] == item["ID"] for r in results):
                        results.append(item)
        except Exception as e:
            logger.warning("Ошибка поиска контактов в Б24: %s", e)
    return results[:limit]


async def get_company(company_id: str | int) -> dict | None:
    """Получает детали компании по ID, включая контакты."""
    if not BITRIX_WEBHOOK_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _url("crm.company.get"),
                params={"id": company_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result")
    except Exception as e:
        logger.warning("Ошибка получения компании %s из Б24: %s", company_id, e)
        return None


# ---------------------------------------------------------------------------
# Основная функция — строит CRM-контекст для промпта
# ---------------------------------------------------------------------------

async def build_crm_context(prompt: str) -> str | None:
    """
    Анализирует промпт, запрашивает данные из Bitrix24 CRM
    и возвращает строку с контекстом для включения в промпт.

    Возвращает None если данные не нужны или Б24 недоступен.
    """
    if not BITRIX_WEBHOOK_URL:
        return None
    if not is_crm_query(prompt):
        return None

    parts: list[str] = []
    prompt_lower = prompt.lower()

    # Извлекаем возможный поисковый запрос из промпта
    # Убираем стоп-слова и берём значимую часть
    search_query = _extract_search_query(prompt)

    # --- Поиск компаний ---
    companies: list[dict] = []
    if search_query:
        companies = await search_companies(search_query, limit=5)
    if not companies:
        # Нет конкретного запроса — показываем список
        companies = await list_companies(limit=15)

    if companies:
        parts.append(f"**Компании в Bitrix24 ({len(companies)} найдено):**")
        for c in companies:
            parts.append(_format_company(c))
        parts.append("")

    # --- Поиск контактов (только если был конкретный запрос) ---
    if search_query:
        contacts = await search_contacts(search_query, limit=5)
        if contacts:
            parts.append(f"**Контакты в Bitrix24 ({len(contacts)} найдено):**")
            for c in contacts:
                parts.append(_format_contact(c))
            parts.append("")

    if not parts:
        return None

    return "\n".join([
        "--- Данные из Bitrix24 CRM ---",
        *parts,
        "--- Конец данных CRM ---",
    ])


def _extract_search_query(prompt: str) -> str:
    """
    Извлекает поисковый запрос из промпта.
    Например: "найди компанию Ромашка" → "Ромашка"
    """
    # Убираем общие слова-команды
    stop_words = [
        "найди", "найдите", "покажи", "покажите", "ищи", "ищите",
        "компанию", "компании", "клиента", "клиентов", "контакт", "контакты",
        "организацию", "фирму", "заказчика", "мне", "пожалуйста",
        "в", "из", "по", "что", "кто", "какой", "какая", "какие",
        "все", "всех", "всё", "список", "перечень",
    ]
    words = prompt.lower().split()
    meaningful = [w for w in words if w not in stop_words and len(w) > 2]

    # Убираем знаки препинания
    meaningful = [re.sub(r"[?!.,;:]", "", w) for w in meaningful]
    meaningful = [w for w in meaningful if w]

    # Если осталось мало слов или это общие термины — не поисковый запрос
    generic_terms = {"клиент", "клиенты", "компания", "компании", "контакт",
                     "контакты", "список", "все", "покажи", "найди"}
    meaningful = [w for w in meaningful if w not in generic_terms]

    return " ".join(meaningful[:3]) if meaningful else ""
