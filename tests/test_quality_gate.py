"""Тесты для worker/quality_gate.py — эвристическая оценка результата задачи.

Чистая логика без внешних зависимостей — моки не нужны.
"""
import pytest

from worker.quality_gate import evaluate, should_retry, MAX_RETRIES


# ---------------------------------------------------------------------------
# Тест 1: happy path — нормальный результат проходит gate
# ---------------------------------------------------------------------------

def test_evaluate_happy_path():
    """Нормальный достаточный текст → passed=True, score >= 4."""
    result = "Задача выполнена успешно. " * 5  # > 50 символов
    ev = evaluate(result)

    assert ev["passed"] is True
    assert ev["score"] >= 4
    assert isinstance(ev["issues"], list)
    assert isinstance(ev["suggestion"], str)


# ---------------------------------------------------------------------------
# Тест 2: пустой результат → провал
# ---------------------------------------------------------------------------

def test_evaluate_empty_result():
    """Пустая строка → passed=False, score=0."""
    ev = evaluate("")

    assert ev["passed"] is False
    assert ev["score"] == 0
    assert len(ev["issues"]) > 0


# ---------------------------------------------------------------------------
# Тест 3: слишком короткий результат (< 50 символов) → провал
# ---------------------------------------------------------------------------

def test_evaluate_short_result():
    """Результат короче 50 символов → провал."""
    ev = evaluate("ok")

    assert ev["passed"] is False
    assert ev["score"] == 0


# ---------------------------------------------------------------------------
# Тест 4: None → провал (не падаем с AttributeError)
# ---------------------------------------------------------------------------

def test_evaluate_none_result():
    """None вместо строки → провал, не исключение."""
    ev = evaluate(None)

    assert ev["passed"] is False
    assert ev["score"] == 0


# ---------------------------------------------------------------------------
# Тест 5: ключевые слова ошибок в хвосте снижают score
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fail_keyword", [
    "не могу", "не удалось", "ошибка", "error", "failed",
    "exception", "traceback", "cannot", "unable to",
])
def test_evaluate_fail_keyword_in_tail(fail_keyword):
    """Ключевое слово провала в последних 300 символах → score снижается."""
    # Достаточный текст впереди + слово провала в конце
    prefix = "Выполняю задачу. " * 10
    result = prefix + " " + fail_keyword

    ev = evaluate(result)

    # Score должен быть снижен — issue зафиксирован
    assert len(ev["issues"]) > 0 or ev["score"] < 5


# ---------------------------------------------------------------------------
# Тест 6: ключевые слова успеха поднимают score
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("success_keyword", [
    "готово", "done", "выполнено", "завершено", "✅",
    "успешно", "completed", "finished",
])
def test_evaluate_success_keyword_bonus(success_keyword):
    """Ключевое слово успеха в хвосте даёт бонус к score."""
    prefix = "Выполняю задачу, всё идёт хорошо. " * 5
    result = prefix + " " + success_keyword

    ev = evaluate(result)

    # Score с бонусом должен быть выше базового 5
    assert ev["score"] >= 5
    assert ev["passed"] is True


# ---------------------------------------------------------------------------
# Тест 7: результат без ключевых слов → базовый score=5, passed
# ---------------------------------------------------------------------------

def test_evaluate_neutral_result():
    """Нейтральный достаточный результат без маркеров → score=5, passed."""
    result = "Здесь нейтральный текст без маркеров провала или успеха. " * 3

    ev = evaluate(result)

    assert ev["score"] == 5
    assert ev["passed"] is True
    assert ev["issues"] == []


# ---------------------------------------------------------------------------
# Тест 8: suggestion непустой при провале
# ---------------------------------------------------------------------------

def test_evaluate_suggestion_on_failure():
    """При провале suggestion содержит подсказку для повтора."""
    ev = evaluate("")  # пустой → точно провал

    assert ev["passed"] is False
    assert len(ev["suggestion"]) > 0


# ---------------------------------------------------------------------------
# Тест 9: score всегда в диапазоне [0, 10]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("result", [
    "",
    "короткий",
    "нейтральный текст без маркеров достаточной длины для прохождения gate " * 2,
    "всё сломалось error traceback failed не удалось " * 5,
    "✅ готово завершено успешно done completed finished выполнено " * 5,
])
def test_evaluate_score_range(result):
    """Score всегда в границах [0, 10]."""
    ev = evaluate(result)
    assert 0 <= ev["score"] <= 10


# ---------------------------------------------------------------------------
# Тест 10: should_retry — логика повторного запуска
# ---------------------------------------------------------------------------

def test_should_retry_when_failed_and_retries_left():
    """Провал + retry_count < MAX_RETRIES → нужен повтор."""
    failed_eval = {"passed": False}
    assert should_retry(failed_eval, retry_count=0) is True
    assert should_retry(failed_eval, retry_count=MAX_RETRIES - 1) is True


def test_should_retry_when_max_retries_reached():
    """Достигнут лимит retry → не повторяем."""
    failed_eval = {"passed": False}
    assert should_retry(failed_eval, retry_count=MAX_RETRIES) is False


def test_should_retry_when_passed():
    """Успешный результат → повтор не нужен, даже если retry_count=0."""
    passed_eval = {"passed": True}
    assert should_retry(passed_eval, retry_count=0) is False
