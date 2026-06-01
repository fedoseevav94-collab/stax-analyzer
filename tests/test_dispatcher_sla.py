from datetime import datetime

import pytest

from app.analyzers.dispatcher_sla import (
    analyze_dispatcher_response_sla,
    count_sla_minutes,
    format_slow_responses_text_report,
    slow_responses_json_report,
)
from app.analyzers import pipeline
from app.config import MoscowTZ


def _dt(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 5, day, hour, minute, tzinfo=MoscowTZ)


def _dt_june(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=MoscowTZ)


def _msg(role: str, text: str, at: datetime, index: int, **extra) -> dict:
    message = {
        "role": role,
        "text": text,
        "created_ts": int(at.timestamp()),
        "message_index": index,
    }
    message.update(extra)
    return message


def _conv(messages: list[dict], dialog_link: str = "https://web.stax.ru/react/telegram/chat/15/123456") -> dict:
    return {
        "conversation_id": "123456",
        "employee": "Иванов Иван",
        "chat_type": "Диспетчеры",
        "source": "telegram",
        "chat_id": "15",
        "dialog_link": dialog_link,
        "messages": messages,
    }


def _slow_response(client_at: datetime, employee_at: datetime | None,
                   check_until: datetime | None = None) -> list[dict]:
    messages = [_msg("client", "текст сообщения водителя", client_at, 1)]
    if employee_at:
        messages.append(_msg(
            "employee",
            "текст ответа диспетчера",
            employee_at,
            2,
            employee_name="Иванов Иван",
        ))
    return analyze_dispatcher_response_sla([_conv(messages)], check_until or employee_at)


@pytest.mark.parametrize(
    ("client_at", "employee_at", "expected_delay", "should_report"),
    [
        (_dt(31, 8, 40), _dt(31, 9, 20), 0, False),
        (_dt(31, 8, 40), _dt(31, 9, 45), 15, False),
        (_dt(31, 8, 40), _dt(31, 9, 55), 25, True),
        (_dt(31, 9, 31), _dt(31, 9, 55), 24, True),
        (_dt(31, 10, 0), _dt(31, 10, 20), 20, True),
        (_dt(31, 20, 50), _dt(31, 21, 10), 10, False),
        (_dt(31, 20, 50), _dt_june(1, 9, 45), 25, True),
        (_dt(31, 22, 30), _dt_june(1, 9, 45), 15, False),
        (_dt(31, 22, 30), _dt_june(1, 9, 55), 25, True),
    ],
)
def test_sla_minutes_and_threshold(client_at, employee_at, expected_delay, should_report):
    assert count_sla_minutes(client_at, employee_at) == expected_delay

    slow_responses = _slow_response(client_at, employee_at)

    if should_report:
        assert len(slow_responses) == 1
        assert slow_responses[0]["delay_minutes"] == expected_delay
        assert slow_responses[0]["status"] == "ответ получен"
        assert slow_responses[0]["conversation_url"] == "https://web.stax.ru/react/telegram/chat/15/123456"
    else:
        assert slow_responses == []


def test_sla_counts_from_first_unanswered_client_message():
    conv = _conv([
        _msg("client", "первый вопрос", _dt(31, 10, 0), 1),
        _msg("client", "уточнение", _dt(31, 10, 5), 2),
        _msg("client", "ещё уточнение", _dt(31, 10, 8), 3),
        _msg("employee", "ответ", _dt(31, 10, 25), 4, employee_name="Петров Пётр"),
    ])

    slow_responses = analyze_dispatcher_response_sla([conv], _dt(31, 10, 25))

    assert len(slow_responses) == 1
    assert slow_responses[0]["delay_minutes"] == 25
    assert slow_responses[0]["driver_message_index"] == 1
    assert slow_responses[0]["dispatcher_name"] == "Петров Пётр"


def test_sla_reports_no_reply_until_export_time():
    slow_responses = _slow_response(_dt(31, 10, 0), None, check_until=_dt(31, 10, 25))

    assert len(slow_responses) == 1
    assert slow_responses[0]["delay_minutes"] == 25
    assert slow_responses[0]["status"] == "нет ответа"
    assert slow_responses[0]["dispatcher_name"] == ""
    assert slow_responses[0]["dispatcher_message_index"] is None


def test_sla_builds_conversation_url_when_dialog_link_is_missing():
    conv = _conv([
        _msg("client", "первый вопрос", _dt(31, 10, 0), 1),
        _msg("employee", "ответ", _dt(31, 10, 25), 2),
    ], dialog_link="")

    slow_responses = analyze_dispatcher_response_sla([conv], _dt(31, 10, 25))

    assert slow_responses[0]["conversation_url"] == "https://web.stax.ru/react/telegram/chat/15/123456"


def test_sla_text_and_json_reports_include_conversation_url():
    period = {
        "report_start_msk": _dt(31, 0, 0),
        "report_end_msk": _dt(31, 19, 0),
    }
    slow_responses = _slow_response(_dt(31, 8, 40), _dt(31, 9, 55))

    text_report = format_slow_responses_text_report(period, slow_responses)
    json_report = slow_responses_json_report(slow_responses)

    assert "Ссылка на диалог: https://web.stax.ru/react/telegram/chat/15/123456" in text_report
    assert json_report["slow_responses"][0]["conversation_url"] == (
        "https://web.stax.ru/react/telegram/chat/15/123456"
    )


def test_pipeline_keeps_sla_separate_from_ai_issues(monkeypatch):
    def fake_analyze_with_ai(candidates):
        return [], {
            "candidates": len(candidates),
            "processed": len(candidates),
            "processed_keys": [],
            "skipped_low_priority": 0,
            "errors": 0,
            "rate_limited": False,
        }

    monkeypatch.setattr(pipeline, "analyze_with_ai", fake_analyze_with_ai)

    issues, _, stats = pipeline.analyze_source(
        [{
            "conversation_id": "123456",
            "employee": "Иванов Иван",
            "messages": [
                _msg("client", "первый вопрос", _dt(31, 10, 0), 1),
                _msg("employee", "ответ диспетчера", _dt(31, 10, 25), 2),
            ],
        }],
        chat_type="Диспетчеры",
        source="telegram",
        chat_id="15",
        check_dispatcher_sla=True,
        sla_check_until=_dt(31, 10, 25),
    )

    assert issues == []
    assert stats["dispatcher_sla_found"] == 1
    assert stats["slow_responses"][0]["delay_minutes"] == 25


def test_dispatcher_reply_without_timestamp_closes_waiting_without_false_no_reply():
    conv = _conv([
        _msg("client", "первый вопрос", _dt(31, 10, 0), 1),
        {"role": "employee", "text": "ответ без времени", "message_index": 2},
    ])

    slow_responses = analyze_dispatcher_response_sla([conv], _dt(31, 10, 45))

    assert slow_responses == []


def test_unknown_role_is_ignored_for_sla():
    conv = _conv([
        _msg("unknown", "первый вопрос", _dt(31, 10, 0), 1),
        _msg("employee", "ответ", _dt(31, 10, 45), 2),
    ])

    slow_responses = analyze_dispatcher_response_sla([conv], _dt(31, 10, 45))

    assert slow_responses == []
