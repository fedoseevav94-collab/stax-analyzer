from app.analyzers.deterministic import check_return_without_retention, has_return_request
from app.analyzers.episodes import prepare_messages


def _conv(messages):
    return {
        "conversation_id": "return-1",
        "employee": "Диспетчер",
        "chat_type": "Диспетчеры",
        "dialog_link": "",
        "messages": prepare_messages(messages),
    }


def test_return_without_retention_triggers_on_scheduling_only_reply():
    issue = check_return_without_retention(_conv([
        {"role": "client", "text": "Хочу сдать машину"},
        {"role": "employee", "text": "На какое время вас записать?"},
    ]))

    assert issue is not None
    problem = issue["problems"][0]
    assert problem["category"] == "СДАЧА_БЕЗ_УДЕРЖАНИЯ"
    assert problem["severity"] == "средняя"
    assert problem["priority"] == "P2"


def test_return_without_retention_skips_when_employee_asks_reason():
    issue = check_return_without_retention(_conv([
        {"role": "client", "text": "Хочу сдать машину"},
        {"role": "employee", "text": "Подскажите, пожалуйста, причину сдачи?"},
    ]))

    assert issue is None


def test_has_return_request_detects_request_even_when_employee_handles_it():
    conv = _conv([
        {"role": "client", "text": "Можно сдать авто завтра?"},
        {"role": "employee", "text": "Подскажите, пожалуйста, причину сдачи?"},
    ])

    assert has_return_request(conv) is True


def test_return_without_retention_skips_dtp_clarification():
    issue = check_return_without_retention(_conv([
        {"role": "client", "text": "Хочу сдать авто после ДТП"},
        {"role": "employee", "text": "Кто был виновен в ДТП?"},
    ]))

    assert issue is None


def test_return_without_retention_does_not_jump_across_episodes():
    issue = check_return_without_retention(_conv([
        {"role": "client", "text": "Хочу сдать машину", "date": "2026-05-27T10:00:00+03:00"},
        {"role": "employee", "text": "На какое время вас записать?", "date": "2026-05-27T17:00:01+03:00"},
    ]))

    assert issue is None


def test_broadened_return_markers_trigger_with_scheduling_only_reply():
    phrases = [
        "запишите на сдачу",
        "можно сдать авто",
        "вернуть автомобиль",
        "закрыть аренду",
        "расторгнуть аренду",
    ]

    for phrase in phrases:
        issue = check_return_without_retention(_conv([
            {"role": "client", "text": phrase},
            {"role": "employee", "text": "На какое время вас записать?"},
        ]))
        assert issue is not None, phrase
        assert issue["problems"][0]["category"] == "СДАЧА_БЕЗ_УДЕРЖАНИЯ"
