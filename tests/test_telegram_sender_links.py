from datetime import datetime

from app.config import MoscowTZ
from app.telegram.sender import (
    _message_link_label,
    build_message_link,
    filter_issues_by_categories,
    format_report,
)


def test_telegram_message_link():
    issue = {
        "source": "telegram",
        "chat_id": "15",
        "conversation_id": "123",
        "dialog_link": "https://web.stax.ru/react/telegram/chat/15/123",
    }
    problem = {"message_id": "999"}

    assert build_message_link(issue, problem) == "https://web.stax.ru/react/telegram/chat/15/123/999"
    assert _message_link_label(issue, problem) == "Сообщение"


def test_client_app_message_link():
    issue = {
        "source": "client_app",
        "conversation_id": "555",
        "dialog_link": "https://web.stax.ru/react/client/chat/555",
    }
    problem = {"message_id": "777"}

    assert build_message_link(issue, problem) == "https://web.stax.ru/react/client/chat/555/777"
    assert _message_link_label(issue, problem) == "Сообщение"


def test_wazzup_falls_back_to_dialog_link():
    issue = {
        "source": "wazzup",
        "conversation_id": "abc",
        "dialog_link": "https://example.com/dialog",
    }
    problem = {"message_id": "999"}

    assert build_message_link(issue, problem) == "https://example.com/dialog"
    assert _message_link_label(issue, problem) == "Диалог"


def test_wazzup_chat_type_falls_back_to_dialog_link_without_source():
    issue = {
        "chat_type": "Wazzup: Основной канал",
        "conversation_id": "abc",
        "dialog_link": "https://example.com/wazzup/dialog",
    }
    problem = {"message_id": "999"}

    assert build_message_link(issue, problem) == "https://example.com/wazzup/dialog"
    assert _message_link_label(issue, problem) == "Диалог"


def test_no_message_id_falls_back_to_dialog_link():
    issue = {
        "source": "telegram",
        "chat_id": "15",
        "conversation_id": "123",
        "dialog_link": "https://web.stax.ru/react/telegram/chat/15/123",
    }
    problem = {}

    assert build_message_link(issue, problem) == "https://web.stax.ru/react/telegram/chat/15/123"
    assert _message_link_label(issue, problem) == "Диалог"


def test_format_report_shows_ai_processing_summary_by_source():
    period = {
        "report_start_msk": datetime(2026, 5, 29, 0, 0, tzinfo=MoscowTZ),
        "report_end_msk": datetime(2026, 5, 29, 13, 3, tzinfo=MoscowTZ),
    }
    analysis_stats = {
        "ai_candidates": 11,
        "ai_processed": 11,
        "skipped_by_filter": 8,
        "return_requests_checked": 4,
        "return_without_retention_found": 1,
        "return_task_cards_loaded": 1,
        "return_task_cards_matched": 1,
        "return_task_cards_unmatched": 0,
        "return_task_retention_found": 1,
        "source_breakdown": [
            {
                "source_name": "Диспетчеры",
                "sent_to_ai": 5,
                "ai_processed": 5,
                "return_requests_checked": 4,
                "return_without_retention_found": 1,
                "return_task_cards_matched": 1,
                "return_task_retention_found": 1,
            },
            {"source_name": "Менеджеры подписок", "sent_to_ai": 1, "ai_processed": 1},
            {"source_name": "Клиентское приложение", "sent_to_ai": 5, "ai_processed": 5},
        ],
    }

    report = format_report([], 0, period, "ok", [], analysis_stats)

    assert "🤖 AI-проверка" in report
    assert "Кандидатов обработано: 11/11" in report
    assert "Пропущено фильтром: 8" in report
    assert "Диспетчеры 5/5" in report
    assert "🧰 Кодовые проверки" in report
    assert "Сдача без удержания: найдено 1 из 4 запросов на сдачу" in report
    assert "Чат задач сдачи: карточек 1, сопоставлено 1, не найдено диалогов 0, проблем 1" in report
    assert "По источникам сдачи: Диспетчеры 1/4" in report


def test_format_report_shows_full_ai_scan_mode():
    period = {
        "report_start_msk": datetime(2026, 5, 29, 0, 0, tzinfo=MoscowTZ),
        "report_end_msk": datetime(2026, 5, 29, 19, 0, tzinfo=MoscowTZ),
    }
    analysis_stats = {
        "ai_candidates": 44,
        "ai_processed": 44,
        "full_ai_scan": True,
    }

    report = format_report([], 0, period, "ok", [], analysis_stats)

    assert "Режим: полный ручной прогон" in report


def test_format_report_shows_queue_mode_without_fake_processing_count():
    period = {
        "report_start_msk": datetime(2026, 5, 29, 0, 0, tzinfo=MoscowTZ),
        "report_end_msk": datetime(2026, 5, 29, 19, 0, tzinfo=MoscowTZ),
    }
    analysis_stats = {
        "ai_candidates": 44,
        "ai_processed": 0,
        "ai_queued": 44,
        "ai_queue_total": 92,
        "ai_mode": "queued",
    }

    report = format_report([], 0, period, "ok", [], analysis_stats, title="🧰 STAX: Кодовые проверки")

    assert report.startswith("🧰 STAX: Кодовые проверки")
    assert "Поставлено в очередь: 44" in report
    assert "Осталось в очереди: 92" in report
    assert "Кандидатов обработано: 0/44" not in report


def test_filter_issues_by_categories_keeps_only_selected_problems():
    issues = [{
        "conversation_id": "1",
        "problems": [
            {"category": "БЕЗ_ОТВЕТА"},
            {"category": "КОНФЛИКТ"},
        ],
    }]

    filtered = filter_issues_by_categories(issues, {"БЕЗ_ОТВЕТА"})

    assert len(filtered) == 1
    assert filtered[0]["problems"] == [{"category": "БЕЗ_ОТВЕТА"}]


def test_format_report_problem_card_is_compact():
    period = {
        "report_start_msk": datetime(2026, 5, 30, 0, 0, tzinfo=MoscowTZ),
        "report_end_msk": datetime(2026, 5, 30, 19, 0, tzinfo=MoscowTZ),
    }
    issue = {
        "conversation_id": "6129",
        "employee": "Глумов Дмитрий",
        "chat_type": "Диспетчеры",
        "source": "telegram",
        "chat_id": "15",
        "dialog_link": "https://web.stax.ru/react/telegram/chat/15/6129",
        "first_client_msg": "У меня стоит блок на неизвестные номера",
        "topic": "Оплата / аренда",
        "problems": [{
            "category": "КОНФЛИКТ",
            "severity": "высокая",
            "priority": "P1",
            "client_quote": "У меня стоит блок на неизвестные номера, не сможет дозвониться. Пусть напишет на вотс ап",
            "employee_quote": "+7 969 131 01 30 наберите ему самостоятельно пожалуйста",
            "description": (
                "Сотрудник повторно предложил клиенту самостоятельно позвонить водителю. "
                "Цитата сотрудника: «+7 969 131 01 30 наберите ему самостоятельно пожалуйста» "
                "Реакция на: «У меня стоит блок на неизвестные номера»"
            ),
        }],
    }

    report = format_report([issue], 1, period, "ok", [], {})

    assert "Клиент:" in report
    assert "Сотрудник:" in report
    assert "Что не так:" in report
    assert report.index("Клиент:") < report.index("Сотрудник:") < report.index("Что не так:")
    assert "Цитата сотрудника:" not in report
    assert "Реакция на:" not in report
    assert "Искать:" in report
    assert "Тема: Оплата / аренда" in report
