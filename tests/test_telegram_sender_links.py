from datetime import datetime

from app.config import MoscowTZ
from app.telegram.sender import _message_link_label, build_message_link, format_report


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
        "source_breakdown": [
            {"source_name": "Диспетчеры", "sent_to_ai": 5, "ai_processed": 5},
            {"source_name": "Менеджеры подписок", "sent_to_ai": 1, "ai_processed": 1},
            {"source_name": "Клиентское приложение", "sent_to_ai": 5, "ai_processed": 5},
        ],
    }

    report = format_report([], 0, period, "ok", [], analysis_stats)

    assert "🤖 AI-проверка" in report
    assert "Кандидатов обработано: 11/11" in report
    assert "Пропущено фильтром: 8" in report
    assert "Диспетчеры 5/5" in report
