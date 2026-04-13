"""
Парсинг PDF через Gemini 2.5 Flash API.

Преимущества перед markitdown/pdfminer:
  - OCR для сканированных PDF
  - Таблицы с мерджами
  - Формулы, рукописный текст
  - Многоязычные документы

API key берётся из remote-config.json (поле gemini_api_key) или из env GEMINI_API_KEY.
Трафик идёт через opera-proxy (trust_env=True → HTTPS_PROXY).
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.0-flash"
MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20 МБ — лимит inline_data Gemini

PROMPT = (
    "Преобразуй содержимое этого PDF в чистый Markdown.\n"
    "Сохрани заголовки, списки, таблицы (| разделители), ссылки.\n"
    "Изображения опиши в [описание]. Формулы в $...$.\n"
    "Не добавляй ничего от себя, не комментируй.\n"
    "Верни только чистый markdown-текст."
)


def parse_pdf(file_path: Path, api_key: str) -> str | None:
    """Парсит PDF через Gemini API. Возвращает markdown текст или None."""
    pdf_bytes = file_path.read_bytes()
    if len(pdf_bytes) > MAX_INLINE_BYTES:
        logger.warning(
            "PDF слишком большой (%.1f МБ > 20 МБ), Gemini не примет",
            len(pdf_bytes) / 1_000_000,
        )
        return None

    body = {
        "contents": [{
            "parts": [
                {
                    "inline_data": {
                        "mime_type": "application/pdf",
                        "data": base64.b64encode(pdf_bytes).decode("ascii"),
                    }
                },
                {"text": PROMPT},
            ]
        }],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 65536},
    }

    for model in (DEFAULT_MODEL, FALLBACK_MODEL):
        try:
            with httpx.Client(timeout=120, trust_env=True) as client:
                r = client.post(
                    f"{API_BASE}/{model}:generateContent",
                    params={"key": api_key},
                    json=body,
                )

            if r.status_code == 429:
                logger.info("Gemini %s rate limit (429), пробую fallback", model)
                continue
            if r.status_code != 200:
                logger.warning(
                    "Gemini %s HTTP %d: %s", model, r.status_code, r.text[:300]
                )
                continue

            data = r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                feedback = data.get("promptFeedback", {})
                logger.warning("Gemini %s пустой ответ: %s", model, feedback)
                continue

            parts = candidates[0].get("content", {}).get("parts", [])
            text = "\n".join(p.get("text", "") for p in parts if "text" in p)
            if not text:
                logger.warning("Gemini %s вернул пустой текст", model)
                continue

            logger.info(
                "PDF распарсен через Gemini %s (%d символов)", model, len(text)
            )
            return text

        except httpx.TimeoutException:
            logger.warning("Gemini %s timeout (120s)", model)
            continue
        except Exception as exc:
            logger.warning("Gemini %s ошибка: %s: %s", model, type(exc).__name__, exc)
            continue

    return None
