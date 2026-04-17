"""Тесты для backend/api/webhooks.py — приём Б24-вебхуков."""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Корень проекта уже добавлен в sys.path через conftest.py
from backend.api.webhooks import router
from backend.core.state import State


@pytest.fixture
def app_with_db(temp_db):
    """FastAPI-приложение с подключённым роутером и временной БД."""
    app = FastAPI()
    app.state.db = temp_db
    app.include_router(router, prefix="/api/webhooks")
    return app


@pytest.fixture
def client(app_with_db):
    """TestClient для приложения."""
    return TestClient(app_with_db)


# ---------------------------------------------------------------------------
# Тест 1: валидный JSON-payload → 200, id в ответе
# ---------------------------------------------------------------------------

def test_webhook_accepted(client):
    """Валидный JSON-payload принимается, возвращается 200 и id."""
    # Отправляем JSON как от Битрикс24
    payload = {"event": "ONCRMCONTACTADD", "data": {"FIELDS": {"ID": "42"}}}
    response = client.post("/api/webhooks/b24", json=payload)

    # Проверяем статус и структуру ответа
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "id" in body
    assert len(body["id"]) == 36  # UUID v4


# ---------------------------------------------------------------------------
# Тест 2: невалидный JSON → всё равно 200 (сохраняется как текст, Б24 не retry-ит)
# ---------------------------------------------------------------------------

def test_webhook_invalid_json(client):
    """Невалидный JSON сохраняется как raw-текст, ответ всё равно 200."""
    # Б24 иногда шлёт form-data или испорченный JSON — webhook должен принять
    response = client.post(
        "/api/webhooks/b24",
        content=b"not a valid json {{{",
        headers={"Content-Type": "application/json"},
    )
    # Эндпоинт всегда возвращает 200 — чтобы Б24 не делал повторных запросов
    assert response.status_code == 200
    assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# Тест 3: пустое тело → сохраняется (пустая строка), ответ 200
# ---------------------------------------------------------------------------

def test_webhook_empty_body(client):
    """Пустое тело принимается (payload сохраняется как пустая строка)."""
    response = client.post(
        "/api/webhooks/b24",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# Тест 4: запись попадает в таблицу webhooks_raw с нужными полями
# ---------------------------------------------------------------------------

def test_webhook_stored_in_db(client, app_with_db):
    """После запроса в webhooks_raw есть запись с source='b24' и processed=0."""
    payload = {"event": "ONCRMLEADADD", "data": {"ID": "7"}}
    response = client.post("/api/webhooks/b24", json=payload)
    assert response.status_code == 200

    webhook_id = response.json()["id"]

    # Проверяем содержимое таблицы
    row = app_with_db.state.db.fetchone(
        "SELECT id, source, payload, processed FROM webhooks_raw WHERE id = ?",
        (webhook_id,),
    )
    assert row is not None, "Запись в webhooks_raw не найдена"
    assert row["source"] == "b24"
    assert row["processed"] == 0

    # Payload должен содержать исходные данные
    stored = json.loads(row["payload"])
    assert stored["event"] == "ONCRMLEADADD"


# ---------------------------------------------------------------------------
# Тест 5: source всегда 'b24' для этого эндпоинта
# ---------------------------------------------------------------------------

def test_webhook_source_is_b24(client, app_with_db):
    """Все записи от этого эндпоинта получают source='b24'."""
    # Отправляем несколько вебхуков
    payloads = [
        {"event": "ONDEALUPDATE", "id": "1"},
        {"event": "ONTASKUPDATE", "id": "2"},
        {"type": "lead_created", "crm_id": 999},
    ]
    for p in payloads:
        resp = client.post("/api/webhooks/b24", json=p)
        assert resp.status_code == 200

    # Проверяем что все записи с source='b24'
    rows = app_with_db.state.db.fetchall(
        "SELECT source FROM webhooks_raw WHERE source != 'b24'"
    )
    assert len(rows) == 0, "Найдены записи с source != 'b24'"


# ---------------------------------------------------------------------------
# Тест 6: каждый вебхук получает уникальный UUID
# ---------------------------------------------------------------------------

def test_webhook_unique_ids(client, app_with_db):
    """Каждый вебхук получает уникальный id."""
    ids = set()
    for i in range(5):
        resp = client.post("/api/webhooks/b24", json={"seq": i})
        assert resp.status_code == 200
        ids.add(resp.json()["id"])

    # Все id уникальные
    assert len(ids) == 5
