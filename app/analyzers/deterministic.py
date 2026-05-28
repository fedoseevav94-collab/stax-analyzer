"""
Детерминированные проверки — без AI, по правилам.
"""
from app.utils.text import clean_text, is_substantive_client_message
from app.logger import logger

LONG_REPLY_SECONDS = 15 * 60
CRITICAL_LONG_REPLY_SECONDS = 60 * 60


def has_employee_reply(messages: list) -> bool:
    return any(m.get("role") == "employee" and clean_text(m.get("text")) for m in messages)


def has_client_message(messages: list) -> bool:
    return any(m.get("role") == "client" and clean_text(m.get("text")) for m in messages)


def has_substantive_client_message(messages: list) -> bool:
    return any(
        m.get("role") == "client" and is_substantive_client_message(m.get("text"))
        for m in messages
    )


def first_client_message(messages: list) -> str:
    for m in messages:
        if m.get("role") == "client":
            return clean_text(m.get("text"))[:160]
    return ""


def _message_ts(message: dict) -> int | None:
    value = message.get("date", message.get("created_date"))
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return ts


def _problem(category: str, description: str, severity: str, priority: str,
             client_quote: str = "", employee_quote: str = "", confidence: float = 1.0) -> dict:
    return {
        "category": category,
        "description": description,
        "severity": severity,
        "confidence": confidence,
        "employee_quote": employee_quote,
        "client_quote": client_quote,
        "priority": priority,
    }


def _issue(conv: dict, problems: list) -> dict:
    return {
        "conversation_id": conv["conversation_id"],
        "employee": conv["employee"],
        "date": conv.get("date", ""),
        "chat_type": conv["chat_type"],
        "dialog_link": conv["dialog_link"],
        "first_client_msg": first_client_message(conv.get("messages", [])),
        "topic": conv.get("topic", "Другое"),
        "problems": problems,
    }


def _format_wait(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} мин."
    hours = minutes // 60
    rest = minutes % 60
    if rest:
        return f"{hours} ч. {rest} мин."
    return f"{hours} ч."


def check_no_reply(conv: dict, analysis_end_ts: int | None = None) -> dict | None:
    """
    Возвращает issue если сотрудник не ответил на содержательное сообщение клиента.
    """
    messages = conv.get("messages", [])
    if not has_client_message(messages):
        return None
    if has_employee_reply(messages):
        return None
    if not has_substantive_client_message(messages):
        logger.info(f"[SKIP БЕЗ_ОТВЕТА] {conv['conversation_id']}: только служебные/закрывающие")
        return None

    logger.info(f"[БЕЗ_ОТВЕТА] {conv['conversation_id']}")
    first_msg = first_client_message(messages)
    category = "БЕЗ_ОТВЕТА"
    description = "Клиент написал, но за период нет ни одного ответа сотрудника."
    last_client_ts = None
    for message in messages:
        if message.get("role") == "client" and is_substantive_client_message(message.get("text")):
            last_client_ts = _message_ts(message)
    if analysis_end_ts and last_client_ts and analysis_end_ts - last_client_ts >= CRITICAL_LONG_REPLY_SECONDS:
        category = "БЕЗ_ОТВЕТА_60М"
        description = f"Клиент ждёт ответа больше 60 минут: {_format_wait(analysis_end_ts - last_client_ts)}."

    return _issue(conv, [_problem(
        category=category,
        description=description,
        severity="высокая",
        priority="P1",
        client_quote=first_msg,
    )])


def check_response_time(conv: dict) -> dict | None:
    """
    Проверяет время от содержательного сообщения клиента до ближайшего ответа сотрудника.
    Возвращает самый серьёзный найденный случай в диалоге.
    """
    messages = conv.get("messages", [])
    timed_messages = [
        (idx, message, _message_ts(message))
        for idx, message in enumerate(messages)
        if _message_ts(message) is not None
    ]
    if not timed_messages:
        return None

    worst_problem = None
    for idx, message, client_ts in timed_messages:
        if message.get("role") != "client" or not is_substantive_client_message(message.get("text")):
            continue

        employee_reply = None
        employee_ts = None
        for next_idx, next_message, next_ts in timed_messages:
            if next_idx <= idx:
                continue
            if next_message.get("role") == "client":
                continue
            if next_message.get("role") == "employee" and clean_text(next_message.get("text")):
                employee_reply = next_message
                employee_ts = next_ts
                break

        if employee_ts is None:
            continue

        wait_seconds = employee_ts - client_ts
        if wait_seconds < LONG_REPLY_SECONDS:
            continue

        client_quote = clean_text(message.get("text"))[:220]
        employee_quote = clean_text(employee_reply.get("text"))[:220]
        if wait_seconds >= CRITICAL_LONG_REPLY_SECONDS:
            problem = _problem(
                category="КРИТИЧЕСКИ_ДОЛГИЙ_ОТВЕТ",
                description=f"Клиент ждал ответа больше 60 минут: {_format_wait(wait_seconds)}.",
                severity="высокая",
                priority="P1",
                client_quote=client_quote,
                employee_quote=employee_quote,
            )
        else:
            problem = _problem(
                category="ДОЛГИЙ_ОТВЕТ",
                description=f"Клиент ждал ответа больше 15 минут: {_format_wait(wait_seconds)}.",
                severity="средняя",
                priority="P2",
                client_quote=client_quote,
                employee_quote=employee_quote,
            )

        if not worst_problem or wait_seconds > worst_problem["_wait_seconds"]:
            problem["_wait_seconds"] = wait_seconds
            worst_problem = problem

    if not worst_problem:
        return None
    worst_problem.pop("_wait_seconds", None)
    logger.info(f"[{worst_problem['category']}] {conv['conversation_id']}")
    return _issue(conv, [worst_problem])
