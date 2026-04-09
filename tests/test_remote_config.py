"""Smoke-тесты для backend.core.remote_config.fetch_remote_config."""
import httpx
import pytest

from backend.core import remote_config


class MockResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class MockClient:
    """Фабрика-билдер: задаём последовательность ответов/исключений."""

    # Класс-уровневые состояния, сбрасываются в фикстуре
    responses: list = []
    calls: int = 0

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        idx = MockClient.calls
        MockClient.calls += 1
        item = MockClient.responses[idx]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _fast_retries_and_reset(monkeypatch):
    """Убираем реальный sleep и сбрасываем MockClient между тестами."""
    monkeypatch.setattr("backend.core.remote_config._RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr("backend.core.remote_config.httpx.Client", MockClient)
    MockClient.responses = []
    MockClient.calls = 0
    yield


def test_success_returns_dict():
    MockClient.responses = [MockResponse(200, '{"version": "1.2", "emoji": "🚀"}')]
    result = remote_config.fetch_remote_config()
    assert result == {"version": "1.2", "emoji": "🚀"}
    assert MockClient.calls == 1


def test_200_non_dict_returns_empty():
    MockClient.responses = [MockResponse(200, '["not", "a", "dict"]')]
    result = remote_config.fetch_remote_config()
    assert result == {}
    assert MockClient.calls == 1


def test_404_returns_empty_no_retry():
    MockClient.responses = [
        MockResponse(404, "not found"),
        MockResponse(200, '{"should": "not reach"}'),
    ]
    result = remote_config.fetch_remote_config()
    assert result == {}
    assert MockClient.calls == 1  # без retry на HTTP-ошибках


def test_retry_recovers_after_connect_errors():
    MockClient.responses = [
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        MockResponse(200, '{"version": "ok"}'),
    ]
    result = remote_config.fetch_remote_config()
    assert result == {"version": "ok"}
    assert MockClient.calls == 3


def test_retry_exhausted_returns_empty():
    MockClient.responses = [httpx.ConnectError("refused")] * 4
    result = remote_config.fetch_remote_config()
    assert result == {}
    assert MockClient.calls == 4  # 1 + 3 retry


def test_invalid_json_returns_empty():
    MockClient.responses = [MockResponse(200, "not json at all {{{")]
    result = remote_config.fetch_remote_config()
    assert result == {}
