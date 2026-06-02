from datetime import datetime

from app.analyzers.deterministic import check_no_reply, has_employee_reply
from app.analyzers.episodes import prepare_messages
from app.config import MoscowTZ


def _conv(messages):
    return {
        "conversation_id": "no-reply-1",
        "employee": "Диспетчер",
        "chat_type": "Диспетчеры",
        "dialog_link": "",
        "messages": prepare_messages(messages),
    }


def _ts(hour: int, minute: int) -> int:
    return int(datetime(2026, 6, 1, hour, minute, tzinfo=MoscowTZ).timestamp())


def test_no_reply_skips_when_employee_answers_by_voice_message():
    conv = _conv([
        {"role": "client", "text": "У меня такой вопрос 17,6 л на 100км, не слишком много?"},
        {"role": "employee", "text": "", "type": "voice", "duration": 18},
    ])

    assert has_employee_reply(conv["messages"]) is True
    assert check_no_reply(conv) is None


def test_no_reply_skips_when_employee_answers_by_ptt_message():
    conv = _conv([
        {"role": "client", "text": "У меня такой вопрос 17,6 л на 100км, не слишком много?"},
        {"role": "employee", "text": "", "message_type": "ptt", "duration": 18},
    ])

    assert has_employee_reply(conv["messages"]) is True
    assert check_no_reply(conv) is None


def test_no_reply_skips_when_employee_answers_with_attachment_payload():
    conv = _conv([
        {"role": "client", "text": "Пришлите инструкцию"},
        {"role": "employee", "text": "", "attachments": [{"type": "document", "name": "instruction.pdf"}]},
    ])

    assert has_employee_reply(conv["messages"]) is True
    assert check_no_reply(conv) is None


def test_no_reply_still_triggers_without_employee_reply_content():
    conv = _conv([
        {"role": "client", "text": "Подскажите, пожалуйста, как решить вопрос?"},
        {"role": "employee", "text": ""},
    ])

    assert has_employee_reply(conv["messages"]) is False
    assert check_no_reply(conv) is not None


def test_no_reply_skips_late_message_before_sla_threshold():
    conv = _conv([
        {"role": "client", "text": "Здравствуйте, хочу приобрести услугу", "created_date": _ts(20, 50)},
    ])
    check_until = datetime(2026, 6, 1, 21, 0, tzinfo=MoscowTZ)

    assert check_no_reply(conv, check_until=check_until) is None


def test_no_reply_triggers_when_sla_threshold_reached():
    conv = _conv([
        {"role": "client", "text": "Здравствуйте, хочу приобрести услугу", "created_date": _ts(20, 30)},
    ])
    check_until = datetime(2026, 6, 1, 21, 0, tzinfo=MoscowTZ)

    issue = check_no_reply(conv, check_until=check_until)

    assert issue is not None
    assert issue["problems"][0]["category"] == "БЕЗ_ОТВЕТА"
