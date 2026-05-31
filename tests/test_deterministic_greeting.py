from app.analyzers.deterministic import check_no_greeting
from app.analyzers.episodes import prepare_messages


def _conv(messages):
    return {
        "conversation_id": "greeting-1",
        "employee": "София",
        "chat_type": "Клиентское приложение",
        "dialog_link": "",
        "messages": prepare_messages(messages),
    }


def test_no_greeting_skips_when_opening_employee_burst_has_greeting():
    issue = check_no_greeting(_conv([
        {"role": "client", "text": "Здравствуйте, хочу приобрести услугу"},
        {"role": "employee", "text": "На какой автомобиль вы рассматриваете оформление?"},
        {"role": "employee", "text": "Добрый день 🙂 Рады оформить для вас лицензию!"},
    ]))

    assert issue is None


def test_no_greeting_triggers_when_opening_employee_burst_has_no_greeting():
    issue = check_no_greeting(_conv([
        {"role": "client", "text": "Здравствуйте, хочу приобрести услугу"},
        {"role": "employee", "text": "На какой автомобиль вы рассматриваете оформление?"},
    ]))

    assert issue is not None
    assert issue["problems"][0]["category"] == "БЕЗ_ПРИВЕТСТВИЯ"
