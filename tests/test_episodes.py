from app.analyzers.episodes import (
    conversation_last_message_key,
    prepare_messages,
)


def test_message_indexes_preserve_input_order_without_timestamps():
    messages = prepare_messages([
        {"role": "client", "text": "Первое"},
        {"role": "employee", "text": "Второе"},
        {"role": "client", "text": "Третье"},
    ])

    assert [m["text"] for m in messages] == ["Первое", "Второе", "Третье"]
    assert [m["message_index"] for m in messages] == [1, 2, 3]
    assert all(m.get("episode_id") for m in messages)


def test_messages_are_sorted_when_all_have_timestamps():
    messages = prepare_messages([
        {"role": "client", "text": "Позже", "date": 300},
        {"role": "employee", "text": "Раньше", "date": 100},
        {"role": "client", "text": "Середина", "date": 200},
    ])

    assert [m["text"] for m in messages] == ["Раньше", "Середина", "Позже"]
    assert [m["message_index"] for m in messages] == [1, 2, 3]


def test_new_episode_on_new_moscow_day():
    messages = prepare_messages([
        {"role": "client", "text": "День 1", "date": "2026-05-27T23:59:00+03:00"},
        {"role": "employee", "text": "День 2", "date": "2026-05-28T00:00:00+03:00"},
    ])

    assert messages[0]["episode_id"] != messages[1]["episode_id"]


def test_new_episode_after_large_same_day_gap():
    messages = prepare_messages([
        {"role": "client", "text": "Утро", "date": "2026-05-27T06:00:00+03:00"},
        {"role": "employee", "text": "День", "date": "2026-05-27T12:00:01+03:00"},
    ])

    assert messages[0]["episode_id"] != messages[1]["episode_id"]


def test_message_id_is_normalized_from_id_or_message_id():
    messages = prepare_messages([
        {"role": "client", "text": "Есть id", "id": 123},
        {"role": "employee", "text": "Есть message_id", "message_id": "abc"},
    ])

    assert messages[0]["message_id"] == "123"
    assert messages[1]["message_id"] == "abc"


def test_last_message_key_changes_when_new_final_message_appears():
    base = prepare_messages([
        {"role": "client", "text": "Первое", "date": 100, "id": 1},
        {"role": "employee", "text": "Ответ", "date": 110, "id": 2},
    ])
    extended = prepare_messages([
        {"role": "client", "text": "Первое", "date": 100, "id": 1},
        {"role": "employee", "text": "Ответ", "date": 110, "id": 2},
        {"role": "client", "text": "Новое сообщение", "date": 120, "id": 3},
    ])

    assert conversation_last_message_key(base) != conversation_last_message_key(extended)
