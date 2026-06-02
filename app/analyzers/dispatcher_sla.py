"""
Кодовая проверка скорости ответа диспетчера.

Эта проверка не связана с AI и не создаёт QA-категории общения. Она считает
только SLA-задержки ответа в рабочее время 09:30-21:00 МСК.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.config import CLIENT_APP_FRONTEND_BASE_URL, MoscowTZ, WAZZUP_FRONTEND_BASE_URL
from app.utils.text import clean_text, is_substantive_client_message

SLA_START = time(hour=9, minute=30)
SLA_END = time(hour=21, minute=0)
SLA_THRESHOLD_MINUTES = 20

CLIENT_ROLES = {"client", "driver", "водитель", "клиент"}
DISPATCHER_ROLES = {"employee", "dispatcher", "operator", "диспетчер", "сотрудник"}

DISPATCHER_REPLY_MEDIA_TYPES = {
    "audio", "voice", "video", "video_note", "photo", "image", "document",
    "file", "attachment", "sticker", "ptt", "voice_message", "audio_message",
    "record", "recording",
}
DISPATCHER_REPLY_ATTACHMENT_FIELDS = (
    "audio", "voice", "video", "video_note", "photo", "image", "document",
    "file", "files", "attachments", "attachment", "media", "media_url",
    "file_url", "url", "voice_url", "audio_url", "record_url", "duration",
    "voice_duration", "audio_duration",
)


def _role(message: dict) -> str:
    return clean_text(message.get("role")).lower()


def _is_client_message(message: dict) -> bool:
    return _role(message) in CLIENT_ROLES and is_substantive_client_message(message.get("text"))


def _has_value(value) -> bool:
    return value not in (None, "", [], {})


def _has_dispatcher_reply_content(message: dict) -> bool:
    if _role(message) not in DISPATCHER_ROLES:
        return False
    if clean_text(message.get("text")) or clean_text(message.get("caption")):
        return True
    for field in ("type", "message_type", "content_type", "media_type", "kind"):
        if clean_text(message.get(field)).lower() in DISPATCHER_REPLY_MEDIA_TYPES:
            return True
    return any(_has_value(message.get(field)) for field in DISPATCHER_REPLY_ATTACHMENT_FIELDS)


def _parse_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(MoscowTZ) if value.tzinfo else value.replace(tzinfo=MoscowTZ)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), MoscowTZ)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text), MoscowTZ)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(MoscowTZ) if parsed.tzinfo else parsed.replace(tzinfo=MoscowTZ)


def _message_datetime(message: dict) -> datetime | None:
    for field in ("created_ts", "created_date", "date", "timestamp", "created_at"):
        parsed = _parse_datetime(message.get(field))
        if parsed:
            return parsed
    return None


def _message_index(message: dict) -> int | None:
    try:
        return int(message.get("message_index"))
    except (TypeError, ValueError):
        return None


def _sort_messages(messages: list[dict]) -> list[dict]:
    dated = [(m, _message_datetime(m)) for m in messages or []]
    if dated and all(dt is not None for _, dt in dated):
        return [m for m, _ in sorted(dated, key=lambda item: (item[1], _message_index(item[0]) or 0))]
    return sorted(messages or [], key=lambda m: _message_index(m) or 0)


def _interval_for_day(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, SLA_START, tzinfo=MoscowTZ),
        datetime.combine(day, SLA_END, tzinfo=MoscowTZ),
    )


def count_sla_minutes(start_dt: datetime, end_dt: datetime) -> int:
    """
    Считает минуты только внутри 09:30-21:00 МСК. Порог дальше проверяется
    включительно: 20 минут и более.
    """
    start = start_dt.astimezone(MoscowTZ) if start_dt.tzinfo else start_dt.replace(tzinfo=MoscowTZ)
    end = end_dt.astimezone(MoscowTZ) if end_dt.tzinfo else end_dt.replace(tzinfo=MoscowTZ)
    if end <= start:
        return 0

    total_seconds = 0
    current_day = start.date()
    while current_day <= end.date():
        work_start, work_end = _interval_for_day(current_day)
        overlap_start = max(start, work_start)
        overlap_end = min(end, work_end)
        if overlap_end > overlap_start:
            total_seconds += int((overlap_end - overlap_start).total_seconds())
        current_day += timedelta(days=1)

    return total_seconds // 60


def _effective_sla_start(start_dt: datetime) -> datetime:
    start = start_dt.astimezone(MoscowTZ) if start_dt.tzinfo else start_dt.replace(tzinfo=MoscowTZ)
    work_start, work_end = _interval_for_day(start.date())
    if start < work_start:
        return work_start
    if start >= work_end:
        next_day = start.date() + timedelta(days=1)
        return _interval_for_day(next_day)[0]
    return start


def _format_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(MoscowTZ).strftime("%d.%m.%y %H:%M")


def _quote_from_message(message: dict | None) -> str:
    if not message:
        return ""
    return (
        clean_text(message.get("text"))
        or clean_text(message.get("caption"))
        or "[медиа-сообщение]"
    )


def _dispatcher_name(message: dict | None, conv: dict) -> str:
    if message:
        for field in (
            "employee", "employee_name", "dispatcher", "dispatcher_name",
            "operator", "operator_name", "author", "author_name",
            "sender", "sender_name", "name",
        ):
            value = clean_text(message.get(field))
            if value:
                return value
    employee = clean_text(conv.get("employee"))
    return "" if employee == "Без ответа" else employee


def _conversation_url(conv: dict) -> str:
    ready = clean_text(conv.get("dialog_link")) or clean_text(conv.get("conversation_url"))
    if ready:
        return ready

    conv_id = clean_text(conv.get("conversation_id"))
    if not conv_id:
        return ""

    source = clean_text(conv.get("source")).lower()
    chat_id = clean_text(conv.get("chat_id"))
    channel_id = clean_text(conv.get("channel_id"))
    if source == "telegram" and chat_id:
        return f"https://web.stax.ru/react/telegram/chat/{chat_id}/{conv_id}"
    if source == "client_app" and CLIENT_APP_FRONTEND_BASE_URL:
        return f"{CLIENT_APP_FRONTEND_BASE_URL}/{conv_id}"
    if source == "wazzup" and WAZZUP_FRONTEND_BASE_URL and channel_id:
        return f"{WAZZUP_FRONTEND_BASE_URL}/{channel_id}/{conv_id}"
    return ""


def _calculation_comment(start_dt: datetime, end_dt: datetime, no_reply: bool) -> str:
    if no_reply:
        return "Ответа нет, задержка считалась до времени выгрузки"

    effective_start = _effective_sla_start(start_dt)
    if start_dt.astimezone(MoscowTZ) < _interval_for_day(start_dt.astimezone(MoscowTZ).date())[0]:
        return (
            "Сообщение пришло до 09:30, задержка считалась "
            f"с {effective_start:%H:%M} до {end_dt.astimezone(MoscowTZ):%H:%M}"
        )
    if effective_start.date() > start_dt.astimezone(MoscowTZ).date():
        return "Сообщение пришло после 21:00, задержка считалась с 09:30 следующего дня"
    if start_dt.astimezone(MoscowTZ).date() != end_dt.astimezone(MoscowTZ).date():
        return "Ночное время 21:00-09:30 не учитывалось"
    return "Задержка считалась только внутри рабочего SLA-времени 09:30-21:00 МСК"


def _record(conv: dict, driver_message: dict, dispatcher_message: dict | None,
            end_dt: datetime, delay_minutes: int) -> dict:
    driver_dt = _message_datetime(driver_message)
    dispatcher_dt = _message_datetime(dispatcher_message) if dispatcher_message else None
    no_reply = dispatcher_message is None
    return {
        "conversation_id": clean_text(conv.get("conversation_id")),
        "conversation_url": _conversation_url(conv),
        "delay_minutes": delay_minutes,
        "status": "нет ответа" if no_reply else "ответ получен",
        "driver_quote": _quote_from_message(driver_message),
        "driver_message_index": _message_index(driver_message),
        "driver_datetime_msk": _format_dt(driver_dt),
        "dispatcher_name": "" if no_reply else _dispatcher_name(dispatcher_message, conv),
        "dispatcher_quote": "" if no_reply else _quote_from_message(dispatcher_message),
        "dispatcher_message_index": None if no_reply else _message_index(dispatcher_message),
        "dispatcher_datetime_msk": "" if no_reply else _format_dt(dispatcher_dt),
        "calculation_comment": _calculation_comment(driver_dt, end_dt, no_reply),
    }


def analyze_dispatcher_response_sla(conversations: list[dict], check_until: datetime) -> list[dict]:
    """
    Возвращает отдельный JSON-готовый список задержек. Не создаёт AI issues.
    """
    slow_responses = []
    check_until_msk = check_until.astimezone(MoscowTZ) if check_until.tzinfo else check_until.replace(tzinfo=MoscowTZ)

    for conv in conversations or []:
        pending_client = None
        for message in _sort_messages(conv.get("messages", [])):
            if _is_client_message(message):
                if pending_client is None:
                    pending_client = message
                continue

            if pending_client is not None and _has_dispatcher_reply_content(message):
                client_dt = _message_datetime(pending_client)
                dispatcher_dt = _message_datetime(message)
                if client_dt and dispatcher_dt:
                    delay = count_sla_minutes(client_dt, dispatcher_dt)
                    if delay >= SLA_THRESHOLD_MINUTES:
                        slow_responses.append(_record(conv, pending_client, message, dispatcher_dt, delay))
                pending_client = None

        if pending_client is not None:
            client_dt = _message_datetime(pending_client)
            if client_dt:
                delay = count_sla_minutes(client_dt, check_until_msk)
                if delay >= SLA_THRESHOLD_MINUTES:
                    slow_responses.append(_record(conv, pending_client, None, check_until_msk, delay))

    return slow_responses


def slow_responses_json_report(slow_responses: list[dict]) -> dict:
    return {"slow_responses": slow_responses or []}


def format_slow_responses_text_report(period: dict, slow_responses: list[dict]) -> str:
    start_msk = period["report_start_msk"]
    period_date = start_msk.strftime("%d.%m.%Y")
    dialog_count = len({item.get("conversation_id") for item in slow_responses or []})

    lines = [
        "ОТЧЁТ ПО ЗАДЕРЖКАМ ОТВЕТОВ ДИСПЕТЧЕРОВ",
        "",
        f"Период: {period_date}",
        "Рабочий день: 09:00–21:00 МСК",
        "Льготный период: 09:00–09:30 МСК",
        "Задержки считаются: 09:30–21:00 МСК",
        f"Порог: {SLA_THRESHOLD_MINUTES} минут и более",
        "",
        f"Всего задержек: {len(slow_responses or [])}",
        f"Диалогов с задержками: {dialog_count}",
    ]

    if not slow_responses:
        lines += ["", "Задержек не обнаружено."]
        return "\n".join(lines)

    for index, item in enumerate(slow_responses, start=1):
        dispatcher_line = "Диспетчер:"
        if item.get("status") == "нет ответа":
            dispatcher_line += "\nнет ответа на момент выгрузки"
        else:
            dispatcher_name = clean_text(item.get("dispatcher_name")) or "Диспетчер"
            dispatcher_line = (
                f"Диспетчер {dispatcher_name} "
                f"(ОТВЕТ {item.get('dispatcher_datetime_msk')}, "
                f"message_index: {item.get('dispatcher_message_index')}):\n"
                f"\"{item.get('dispatcher_quote')}\""
            )

        lines += [
            "",
            "---",
            "",
            f"{index}. conversation_id: {item.get('conversation_id')}",
            f"Ссылка на диалог: {item.get('conversation_url', '')}",
            f"Задержка по ответу: {item.get('delay_minutes')} минут",
            f"Статус: {item.get('status')}",
            "",
            (
                f"Водитель/Клиент ({item.get('driver_datetime_msk')}, "
                f"message_index: {item.get('driver_message_index')}):"
            ),
            f"\"{item.get('driver_quote')}\"",
            "",
            dispatcher_line,
            "",
            "Комментарий расчёта:",
            clean_text(item.get("calculation_comment")),
        ]

    return "\n".join(lines)
