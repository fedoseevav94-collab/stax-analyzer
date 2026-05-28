"""
Детерминированные проверки — без AI, по правилам.
"""
from app.utils.text import clean_text, is_substantive_client_message
from app.logger import logger

CLIENT_GREETINGS = (
    "здравствуйте", "добрый день", "добрый вечер", "доброе утро",
    "привет", "здрасте", "здравствуй", "hello", "hi",
)
EMPLOYEE_GREETINGS = (
    "здравствуйте", "добрый день", "добрый вечер", "доброе утро",
    "привет", "здравствуй",
)
BOT_EMPLOYEES = {"stax система", "stax system"}


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
        "source": conv.get("source", ""),
        "chat_id": conv.get("chat_id", ""),
        "channel_id": conv.get("channel_id", ""),
        "problems": problems,
    }


def _norm(text: str) -> str:
    return clean_text(text).lower().replace("ё", "е")


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = _norm(text)
    return any(phrase in normalized for phrase in phrases)


def _first_non_empty(messages: list) -> tuple[int, dict] | tuple[None, None]:
    for index, message in enumerate(messages):
        if clean_text(message.get("text")):
            return index, message
    return None, None


def _first_employee_reply_after(messages: list, start_index: int) -> dict | None:
    for message in messages[start_index + 1:]:
        if message.get("role") == "employee" and clean_text(message.get("text")):
            return message
    return None


def _is_bot_employee(conv: dict) -> bool:
    return (conv.get("employee") or "").strip().lower() in BOT_EMPLOYEES


def check_no_reply(conv: dict) -> dict | None:
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
    return _issue(conv, [_problem(
        category="БЕЗ_ОТВЕТА",
        description="Клиент написал, но за период нет ни одного ответа сотрудника.",
        severity="высокая",
        priority="P1",
        client_quote=first_msg,
    )])


def check_no_greeting(conv: dict) -> dict | None:
    """
    Безопасно проверяет отсутствие приветствия в первом ответе живого сотрудника.
    """
    if _is_bot_employee(conv):
        return None

    messages = conv.get("messages", [])
    first_index, first_message = _first_non_empty(messages)
    if not first_message or first_message.get("role") != "client":
        return None

    first_client_msg = clean_text(first_message.get("text"))
    if not _has_any(first_client_msg, CLIENT_GREETINGS):
        return None

    first_employee_reply = _first_employee_reply_after(messages, first_index)
    if not first_employee_reply:
        return None

    employee_quote = clean_text(first_employee_reply.get("text"))
    if _has_any(employee_quote, EMPLOYEE_GREETINGS):
        return None

    logger.info(f"[БЕЗ_ПРИВЕТСТВИЯ] {conv['conversation_id']}")
    return _issue(conv, [_problem(
        category="БЕЗ_ПРИВЕТСТВИЯ",
        description="Клиент начал диалог с приветствия, первый ответ сотрудника был без приветствия.",
        severity="низкая",
        priority="P3",
        client_quote=first_client_msg,
        employee_quote=employee_quote,
    )])
