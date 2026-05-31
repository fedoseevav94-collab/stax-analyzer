from app.tools import telegram_return_chat_diagnostic as diagnostic


def test_preview_collapses_and_truncates_text():
    text = "  первая   строка\nвторая строка  "

    assert diagnostic._preview(text, 12) == "первая стро…"


def test_message_helpers_support_message_and_channel_post():
    message = {"message": {"text": "Запись на возврат авто:", "chat": {"id": -1}}}
    channel_post = {"channel_post": {"caption": "карточка", "chat": {"id": -2}}}

    assert diagnostic._message_from_update(message)["text"] == "Запись на возврат авто:"
    assert diagnostic._message_from_update(channel_post)["caption"] == "карточка"
    assert diagnostic._message_text(channel_post["channel_post"]) == "карточка"


def test_sender_label_marks_bot_sender():
    message = {
        "from": {
            "username": "STAXBot",
            "is_bot": True,
        }
    }

    assert diagnostic._sender_label(message) == "STAXBot (bot)"


def test_int_env_falls_back_on_invalid_value(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_UPDATES_LIMIT", "abc")

    assert diagnostic._int_env("TELEGRAM_UPDATES_LIMIT", 100) == 100
    assert "WARNING" in capsys.readouterr().out
