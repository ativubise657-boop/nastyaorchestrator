"""Парсинг PDF и описание изображений через AITunnel → Gemini 2.5 Flash.

OpenAI-совместимый endpoint AITunnel `/v1/chat/completions` с моделью
`gemini-2.5-flash`. Документ передаётся как multimodal part `image_url` с
data-URL `data:<mime>;base64,...` — AITunnel проксирует в нативный формат
Gemini (`inline_data`).

Почему через AITunnel а не напрямую:
  - единый ключ AITUNNEL_API_KEY — не плодим ещё один provider
  - OCR сканированных PDF без локального Tesseract
  - описание картинок когда Codex CLI без vision (gpt-5.3-codex, gpt-5.4)

Ключ из env AITUNNEL_API_KEY (грузится через .env / .secrets.json / БД).
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.aitunnel.ru/v1"
DEFAULT_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.0-flash"
MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20 МБ — лимит inline_data Gemini

PDF_PROMPT = (
    "Преобразуй содержимое этого PDF в чистый Markdown.\n"
    "Сохрани заголовки, списки, таблицы (| разделители), ссылки.\n"
    "Изображения опиши в [описание]. Формулы в $...$.\n"
    "Не добавляй ничего от себя, не комментируй.\n"
    "Верни только чистый markdown-текст."
)

IMAGE_PROMPT = (
    "Опиши подробно что изображено на этом скриншоте/картинке.\n"
    "Если там есть текст — приведи его дословно с форматированием.\n"
    "Если это UI/интерфейс — опиши элементы и что на них написано.\n"
    "Если это схема/диаграмма/график — опиши структуру, подписи и связи.\n"
    "Если это фото — опиши что на нём (объекты, люди, действия, место).\n"
    "Верни результат в markdown. Без вводных фраз вроде «На этой картинке»."
)


def _describe(
    file_path: Path,
    *,
    mime_type: str,
    prompt: str,
    api_key: str | None,
    base_url: str | None,
) -> str | None:
    """Универсальный вызов AITunnel/Gemini для описания бинарного документа.

    Поддерживает любой mime что Gemini принимает в inline_data
    (application/pdf, image/png, image/jpeg, image/webp, image/gif).
    """
    api_key = api_key or os.getenv("AITUNNEL_API_KEY", "")
    if not api_key:
        logger.warning("AITUNNEL_API_KEY не задан — %s через AITunnel не обработать", mime_type)
        return None

    base_url = (base_url or os.getenv("AITUNNEL_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
    file_bytes = file_path.read_bytes()
    if len(file_bytes) > MAX_INLINE_BYTES:
        logger.warning(
            "%s слишком большой (%.1f МБ > 20 МБ), Gemini не примет",
            mime_type, len(file_bytes) / 1_000_000,
        )
        return None

    data_url = f"data:{mime_type};base64,{base64.b64encode(file_bytes).decode('ascii')}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body_base = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
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
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )

            if not content:
                logger.warning("AITunnel/%s вернул пустой текст", model)
                continue

            logger.info(
                "%s распарсен через AITunnel/%s (%d символов)",
                mime_type, model, len(content),
            )
            return content

        except httpx.TimeoutException:
            logger.warning("AITunnel/%s timeout (120s)", model)
            continue
        except Exception as exc:
            logger.warning("AITunnel/%s ошибка: %s: %s", model, type(exc).__name__, exc)
            continue

    return None


def parse_pdf(
    file_path: Path,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str | None:
    """Парсит PDF через AITunnel (Gemini). Возвращает markdown или None при ошибке."""
    return _describe(
        file_path,
        mime_type="application/pdf",
        prompt=PDF_PROMPT,
        api_key=api_key,
        base_url=base_url,
    )


def parse_image(
    file_path: Path,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str | None:
    """Описывает изображение (PNG/JPG/WebP/GIF) через AITunnel Gemini.

    Используется когда Codex CLI без vision: в промпт кладётся text-описание,
    модель видит содержимое скриншота как текст.
    """
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        mime_type = "image/png"
    return _describe(
        file_path,
        mime_type=mime_type,
        prompt=IMAGE_PROMPT,
        api_key=api_key,
        base_url=base_url,
    )
