from app.analyzers.deterministic import check_no_reply, has_employee_reply
from app.analyzers.episodes import prepare_messages


def _conv(messages):
    return {
        "conversation_id": "no-reply-1",
        "employee": "Диспетчер",
        "chat_type": "Диспетчеры",
        "dialog_link": "",
        "messages": prepare_messages(messages),
    }


def test_no_reply_skips_when_employee_answers_by_voice_message():
    conv = _conv([
        {"role": "client", "text": "У меня такой вопрос 17,6 л на 100км, не слишком много?"},
        {"role": "employee", "text": "", "type": "voice", "duration": 18},
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
