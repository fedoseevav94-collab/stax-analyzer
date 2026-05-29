from app.telegram.sender import _message_link_label, build_message_link


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
