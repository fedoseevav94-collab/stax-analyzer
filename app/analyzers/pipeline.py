"""
Пайплайн анализа одного источника диалогов.
"""
from app.analyzers.deterministic import check_no_reply, has_client_message, has_employee_reply
from app.analyzers.ai_analyzer import analyze_with_ai
from app.config import WAZZUP_FRONTEND_BASE_URL, CLIENT_APP_FRONTEND_BASE_URL
from app.logger import logger
from app.utils.text import clean_text


def _build_dialog_link(source: str, conv_id: str, chat_id: str = "", channel_id: str = "") -> str:
    if not conv_id:
        return ""
    if source == "telegram" and chat_id:
        return f"https://web.stax.ru/react/telegram/chat/{chat_id}/{conv_id}"
    if source == "wazzup" and WAZZUP_FRONTEND_BASE_URL and channel_id:
        return f"{WAZZUP_FRONTEND_BASE_URL}/{channel_id}/{conv_id}"
    if source == "client_app" and CLIENT_APP_FRONTEND_BASE_URL:
        return f"{CLIENT_APP_FRONTEND_BASE_URL}/{conv_id}"
    return ""


def _normalize_conversation(conv: dict, chat_type: str, source: str,
                             chat_id: str = "", channel_id: str = "") -> dict:
    messages = conv.get("messages", []) or []
    conv_id = str(conv.get("conversation_id", "")).strip()
    first_msg = ""
    for m in messages:
        if m.get("role") == "client":
            first_msg = clean_text(m.get("text"))[:160]
            break
    return {
        "conversation_id": conv_id,
        "employee": conv.get("employee") or "Без ответа",
        "date": conv.get("date", ""),
        "chat_type": chat_type,
        "source": source,
        "chat_id": chat_id,
        "channel_id": channel_id,
        "dialog_link": _build_dialog_link(source, conv_id, chat_id, channel_id),
        "first_client_msg": first_msg,
        "messages": messages,
    }


def analyze_source(conversations: list, chat_type: str, source: str,
                   chat_id: str = "", channel_id: str = "") -> tuple[list, int]:
    """
    Возвращает (issues, total_dialogs_count).
    """
    normalized = [_normalize_conversation(c, chat_type, source, chat_id, channel_id)
                  for c in conversations]

    # Дедуп по conversation_id (берём с наибольшим числом сообщений)
    by_id: dict = {}
    for c in normalized:
        cid = c["conversation_id"]
        if not cid:
            continue
        if cid not in by_id or len(c["messages"]) > len(by_id[cid]["messages"]):
            by_id[cid] = c

    deduped = list(by_id.values())
    if len(deduped) < len(normalized):
        logger.info(f"Дедуп выгрузки: {len(normalized)} → {len(deduped)} диалогов")

    all_issues: list = []
    ai_candidates: list = []

    # Боты — не анализируем через AI
    BOT_EMPLOYEES = {"stax система", "stax system"}

    for conv in deduped:
        if not has_client_message(conv["messages"]):
            continue

        emp = (conv.get("employee") or "").strip().lower()
        is_bot = emp in BOT_EMPLOYEES

        no_reply_issue = check_no_reply(conv)
        if no_reply_issue:
            all_issues.append(no_reply_issue)
        elif has_employee_reply(conv["messages"]) and not is_bot:
            ai_candidates.append(conv)
        elif is_bot:
            logger.info(f"[SKIP BOT] {conv['conversation_id']}: сотрудник={conv.get('employee')}")

    ai_issues = analyze_with_ai(ai_candidates)
    all_issues.extend(ai_issues)

    return all_issues, len(deduped)
