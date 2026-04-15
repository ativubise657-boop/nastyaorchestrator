"""Парсинг PDF через AITunnel → Gemini 2.5 Flash.

OpenAI-совместимый endpoint AITunnel `/v1/chat/completions` с моделью
`gemini-2.5-flash`. PDF передаётся как multimodal part `image_url` с
data-URL `data:application/pdf;base64,...` — AITunnel проксирует это в
нативный формат Gemini (`inline_data`).

Преимущества каскадного уровня (markitdown/pdfminer → AITunnel):
  - OCR для сканированных PDF (без локального Tesseract)
  - Таблицы с мерджами, формулы, рукописный текст
  - Единый ключ AITUNNEL_API_KEY — не плодим ещё один provider

Ключ из env AITUNNEL_API_KEY (грузится через .env в backend/core/config.py).
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.aitunnel.ru/v1"
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


def parse_pdf(
    file_path: Path,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str | None:
    """Парсит PDF через AITunnel (Gemini). Возвращает markdown или None при ошибке."""
    api_key = api_key or os.getenv("AITUNNEL_API_KEY", "")
    if not api_key:
        logger.warning("AITUNNEL_API_KEY не задан — PDF через AITunnel не распарсить")
        return None

    base_url = (base_url or os.getenv("AITUNNEL_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
    pdf_bytes = file_path.read_bytes()
    if len(pdf_bytes) > MAX_INLINE_BYTES:
        logger.warning(
            "PDF слишком большой (%.1f МБ > 20 МБ), Gemini не примет",
            len(pdf_bytes) / 1_000_000,
        )
        return None

    data_url = f"data:application/pdf;base64,{base64.b64encode(pdf_bytes).decode('ascii')}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body_base = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 65536,
    }

    for model in (DEFAULT_MODEL, FALLBACK_MODEL):
        body = {**body_base, "model": model}
        try:
            with httpx.Client(timeout=120, trust_env=True) as client:
                r = client.post(f"{base_url}/chat/completions", json=body, headers=headers)

            if r.status_code == 429:
                logger.info("AITunnel/%s rate limit (429), пробую fallback", model)
                continue
            if r.status_code != 200:
                logger.warning(
                    "AITunnel/%s HTTP %d: %s", model, r.status_code, r.text[:300]
                )
                continue

            data = r.json()
            choices = data.get("choices", [])
            if not choices:
                logger.warning("AITunnel/%s пустой ответ: %s", model, data)
                continue

            content = choices[0].get("message", {}).get("content", "")
            # Некоторые провайдеры возвращают content как list-of-parts
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )

            if not content:
                logger.warning("AITunnel/%s вернул пустой текст", model)
                continue

            logger.info(
                "PDF распарсен через AITunnel/%s (%d символов)", model, len(content)
            )
            return content

        except httpx.TimeoutException:
            logger.warning("AITunnel/%s timeout (120s)", model)
            continue
        except Exception as exc:
            logger.warning("AITunnel/%s ошибка: %s: %s", model, type(exc).__name__, exc)
            continue

    return None
