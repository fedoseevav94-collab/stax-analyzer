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
RETURN_REQUEST_MARKERS = (
    "записаться на сдачу", "запишите на сдачу", "записать на сдачу",
    "хочу сдать", "буду сдавать", "еду на сдачу", "поеду на сдачу",
    "могу сдать", "смогу сдать", "можно сдать", "сдать сегодня",
    "сдать завтра", "сдать или", "на сдачу",
    "хочу сдать машину", "хочу сдать авто", "хочу сдать автомобиль",
    "хочу вернуть машину", "хочу вернуть авто", "хочу вернуть автомобиль",
    "буду сдавать машину", "буду сдавать авто", "буду сдавать автомобиль",
    "смогу сдать машину", "смогу сдать авто", "смогу сдать автомобиль",
    "могу сдать машину", "могу сдать авто", "могу сдать автомобиль",
    "можно записаться", "запишите меня", "запишите пожалуйста",
    "записаться на возврат", "запишите на возврат", "записать на возврат",
    "возврат машины", "возврат авто", "возврат автомобиля",
    "вернуть машину", "вернуть авто", "вернуть автомобиль",
    "сдавать машину", "сдавать авто", "сдавать автомобиль",
    "сдаю машину", "сдаю авто", "сдаю автомобиль",
    "сдам машину", "сдам авто", "сдам автомобиль",
    "снять с аренды", "снимаюсь с аренды", "закрыть аренду",
    "закрываю аренду", "расторгнуть аренду", "расторгаю аренду",
    "отказаться от машины", "отказываюсь от машины", "отказаться от авто",
    "сдать авто", "сдать автомобиль", "сдать машину", "сдача авто",
    "сдача автомобиля", "сдача машины", "вернуть авто", "вернуть автомобиль",
)
SCHEDULING_ONLY_MARKERS = (
    "на какое время", "на какой день", "когда записать", "когда вас записать",
    "во сколько записать", "во сколько вас записать", "какое время записать",
    "на когда записать", "сегодня или завтра", "завтра утром", "завтра днем",
    "завтра днём", "завтра вечером", "во сколько", "когда удобно",
)
RETENTION_OR_CLARIFICATION_MARKERS = (
    "почему", "причин", "что случилось", "что произошло", "что не устроило",
    "какая проблема", "какие проблемы", "можем помочь", "давайте разбер",
    "разберемся", "разберёмся", "уточните", "подскажите", "предлож",
    "альтернатив", "другой автомобиль", "замен", "ремонт", "сервис",
    "попробуем решить", "решить вопрос", "можно оставить", "не сдавайте",
    "кто был виновен", "кто виновен", "кто виноват", "дтп",
)


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


def _first_client_message_obj(messages: list) -> dict | None:
    for m in messages:
        if m.get("role") == "client":
            return m
    return None


def _problem(category: str, description: str, severity: str, priority: str,
             client_quote: str = "", employee_quote: str = "",
             confidence: float = 1.0, message_id: str = "") -> dict:
    problem = {
        "category": category,
        "description": description,
        "severity": severity,
        "confidence": confidence,
        "employee_quote": employee_quote,
        "client_quote": client_quote,
        "priority": priority,
    }
    if message_id:
        problem["message_id"] = message_id
    return problem


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


def _message_id(message: dict | None) -> str:
    if not message:
        return ""
    return clean_text(message.get("message_id")) or clean_text(message.get("id"))


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


def _first_client_return_request(messages: list) -> tuple[int, str] | tuple[None, str]:
    for index, message in enumerate(messages):
        if message.get("role") != "client":
            continue
        text = clean_text(message.get("text"))
        if _has_any(text, RETURN_REQUEST_MARKERS):
            return index, text
    return None, ""


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
    first_client = _first_client_message_obj(messages)
    first_msg = clean_text(first_client.get("text"))[:160] if first_client else ""
    return _issue(conv, [_problem(
        category="БЕЗ_ОТВЕТА",
        description="Клиент написал, но за период нет ни одного ответа сотрудника.",
        severity="высокая",
        priority="P1",
        client_quote=first_msg,
        message_id=_message_id(first_client),
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
        message_id=_message_id(first_employee_reply),
    )])


def check_return_without_retention(conv: dict) -> dict | None:
    """
    Проверяет, что диспетчер не записывает водителя на сдачу без попытки удержания.
    """
    if _is_bot_employee(conv):
        return None

    messages = conv.get("messages", [])
    client_index, client_quote = _first_client_return_request(messages)
    if client_index is None:
        return None

    first_employee_reply = _first_employee_reply_after(messages, client_index)
    if not first_employee_reply:
        logger.info(f"[SKIP СДАЧА_БЕЗ_УДЕРЖАНИЯ] {conv['conversation_id']}: нет ответа сотрудника после просьбы о сдаче")
        return None

    employee_quote = clean_text(first_employee_reply.get("text"))
    if not _has_any(employee_quote, SCHEDULING_ONLY_MARKERS):
        logger.info(f"[SKIP СДАЧА_БЕЗ_УДЕРЖАНИЯ] {conv['conversation_id']}: ответ не только про день/время записи")
        return None
    if _has_any(employee_quote, RETENTION_OR_CLARIFICATION_MARKERS):
        logger.info(f"[SKIP СДАЧА_БЕЗ_УДЕРЖАНИЯ] {conv['conversation_id']}: есть уточнение причины или попытка решения")
        return None

    logger.info(f"[СДАЧА_БЕЗ_УДЕРЖАНИЯ] {conv['conversation_id']}")
    return _issue(conv, [_problem(
        category="СДАЧА_БЕЗ_УДЕРЖАНИЯ",
        description="Водитель просит записаться на сдачу, сотрудник уточняет только день/время и не пытается выяснить причину или предложить решение.",
        severity="средняя",
        priority="P2",
        client_quote=client_quote,
        employee_quote=employee_quote,
        message_id=_message_id(first_employee_reply),
    )])
