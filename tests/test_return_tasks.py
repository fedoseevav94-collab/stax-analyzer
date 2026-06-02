from datetime import datetime

from app.analyzers.episodes import prepare_messages
from app.analyzers.return_tasks import analyze_return_tasks_for_conversations, find_matching_conversation
from app.config import MoscowTZ
from app.exporters.telegram_return_tasks import normalize_plate, parse_return_task_message


RETURN_CARD = """
Запись на возврат авто:

ФИО: Гуркалов Александр Михайлович
Категория водителя: Поддержка
Дата записи: 01.06 10:30
Комментарий (причина возврата): уезжает
Ответственный: Петрович Александр @serb_98
Машина: C145CM761 Hyundai Solaris
Период аренды: с 20.02.2026
Дата начала работы в парке: 08.08.2025

Задача менеджеру: связаться и удержать водителя
"""


def _task():
    message = {
        "message_id": 321,
        "date": int(datetime(2026, 6, 1, 12, 0, tzinfo=MoscowTZ).timestamp()),
        "chat": {"id": -1002393474582},
        "text": RETURN_CARD,
    }
    return parse_return_task_message(message, update_id=10)


def _conv(messages, **extra):
    data = {
        "conversation_id": "777",
        "employee": "Диспетчер",
        "chat_type": "Диспетчеры",
        "source": "telegram",
        "chat_id": "15",
        "dialog_link": "https://web.stax.ru/react/telegram/chat/15/777",
        "search_text": "Гуркалов Александр Михайлович C145CM761 Hyundai Solaris",
        "messages": prepare_messages(messages),
    }
    data.update(extra)
    return data


def test_parse_return_task_card_extracts_driver_and_plate():
    task = _task()

    assert task["full_name"] == "Гуркалов Александр Михайлович"
    assert task["return_reason"] == "уезжает"
    assert task["responsible"] == "Петрович Александр @serb_98"
    assert task["plate"] == "C145CM761"
    assert normalize_plate("С145СМ761") == "C145CM761"


def test_find_matching_conversation_by_name_or_plate():
    task = _task()
    conversations = [
        _conv([], conversation_id="wrong", search_text="Иванов Иван"),
        _conv([], conversation_id="right", search_text="Гуркалов Александр Михайлович"),
    ]

    assert find_matching_conversation(task, conversations)["conversation_id"] == "right"


def test_return_task_triggers_when_dispatcher_only_schedules_return():
    task = _task()
    conv = _conv([
        {"role": "client", "text": "Добрый день"},
        {"role": "employee", "text": "На какое время вас записать?"},
    ])

    issues, stats = analyze_return_tasks_for_conversations([task], [conv])

    assert stats["return_task_cards_matched"] == 1
    assert stats["return_task_retention_found"] == 1
    assert issues[0]["problems"][0]["category"] == "СДАЧА_БЕЗ_УДЕРЖАНИЯ"
    assert "Гуркалов Александр Михайлович" in issues[0]["problems"][0]["client_quote"]


def test_return_task_skips_when_dispatcher_asks_reason():
    task = _task()
    conv = _conv([
        {"role": "client", "text": "Добрый день"},
        {"role": "employee", "text": "Подскажите, пожалуйста, причину сдачи?"},
    ])

    issues, stats = analyze_return_tasks_for_conversations([task], [conv])

    assert issues == []
    assert stats["return_task_cards_matched"] == 1
    assert stats["return_task_retention_found"] == 0


def test_return_task_counts_unmatched_dialog():
    task = _task()

    issues, stats = analyze_return_tasks_for_conversations([task], [_conv([], search_text="Иванов Иван")])

    assert issues == []
    assert stats["return_task_cards_unmatched"] == 1
