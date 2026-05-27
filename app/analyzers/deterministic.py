"""
Детерминированные проверки — без AI, по правилам.
Единственная категория: БЕЗ_ОТВЕТА.
"""
from app.utils.text import clean_text, is_substantive_client_message
from app.logger import logger


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
    return {
        "conversation_id": conv["conversation_id"],
        "employee": conv["employee"],
        "date": conv.get("date", ""),
        "chat_type": conv["chat_type"],
        "dialog_link": conv["dialog_link"],
        "first_client_msg": first_msg,
        "problems": [{
            "category": "БЕЗ_ОТВЕТА",
            "description": "Клиент написал, но за период нет ни одного ответа сотрудника.",
            "severity": "высокая",
            "confidence": 1.0,
            "employee_quote": "",
            "client_quote": first_msg,
        }],
    }
