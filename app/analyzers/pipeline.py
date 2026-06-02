"""
Пайплайн анализа одного источника диалогов.
"""
import re

from app.analyzers.deterministic import (
    check_no_greeting,
    check_no_reply,
    check_return_without_retention,
    has_client_message,
    has_employee_reply,
    has_return_request,
)
from app.analyzers.dispatcher_sla import analyze_dispatcher_response_sla
from app.analyzers.ai_analyzer import analyze_with_ai
from app.analyzers.episodes import conversation_last_message_key, prepare_messages
from app.config import WAZZUP_FRONTEND_BASE_URL, CLIENT_APP_FRONTEND_BASE_URL
from app.logger import logger
from app.utils.text import clean_text, is_substantive_client_message


RISK_PHRASES = (
    "жалоба", "не работает", "не могу", "не получается", "почему",
    "сколько ждать", "не отвечаете", "ужас", "плохо", "отказ",
    "верните деньги", "конкуренты", "уйду", "расторгнуть",
    "проблема не решена", "сколько можно", "обман", "жаловаться",
    "поддержка не отвечает", "никто не отвечает",
)

P1_RISK_PHRASES = (
    "верните деньги", "конкуренты", "уйду", "расторг", "обман",
    "жаловаться", "жалоба", "в суд", "претензия", "никто не отвечает",
    "поддержка не отвечает",
)

NORMAL_SHORT_REPLIES = {
    "спасибо", "пожалуйста", "сдал", "сдала", "принято", "добрый день",
    "добрый вечер", "доброе утро", "здравствуйте", "привет", "хорошо",
    "ок", "окей", "понял", "поняла", "ясно", "да", "нет",
}

TOPIC_RULES = (
    ("Возврат денег", ("вернуть деньги", "верните деньги", "вернуть денежные средства", "возврат денежных средств", "деньги обратно", "не вернете")),
    ("Сдача автомобиля", ("сдать авто", "сдать автомобиль", "сдача авто", "сдача автомобиля", "сдать машину", "сдачи машины", "до 16", "до 11")),
    ("Оплата / аренда", ("аренд", "оплат", "платеж", "платёж", "тариф", "стоимость", "счет", "счёт")),
    ("Ремонт / ДТП", ("ремонт", "дтп", "авар", "поврежд", "страхов", "эвакуатор")),
    ("Штрафы", ("штраф", "пени", "неустойк", "нарушение")),
    ("Документы / путевые листы", ("путев", "документ", "накладн", "заявк", "маршрут", "рейс")),
    ("Техническая проблема", ("не работает", "ошибка", "баг", "не могу", "не получается", "завис", "сломал")),
    ("Другое", ()),
)


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
    messages = prepare_messages(conv.get("messages", []) or [])
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
        "topic": detect_topic(messages),
        "last_message_key": conversation_last_message_key(messages),
        "messages": messages,
    }


def _norm_for_filter(text: str) -> str:
    text = clean_text(text).lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9 ]+", " ", text).strip()


def _dialog_text(messages: list) -> str:
    return " ".join(_norm_for_filter(m.get("text")) for m in messages)


def detect_topic(messages: list) -> str:
    text = _dialog_text(messages)
    for topic, markers in TOPIC_RULES:
        if markers and any(marker in text for marker in markers):
            return topic
    return "Другое"


def _is_obviously_normal_short_dialog(messages: list) -> bool:
    meaningful = [
        _norm_for_filter(m.get("text"))
        for m in messages
        if m.get("role") in {"client", "employee"} and clean_text(m.get("text"))
    ]
    if not meaningful or len(meaningful) > 4:
        return False
    return all(text in NORMAL_SHORT_REPLIES for text in meaningful)


def _max_client_streak(messages: list) -> int:
    max_streak = 0
    current = 0
    current_episode = None
    for m in messages:
        episode_id = m.get("episode_id")
        if episode_id != current_episode:
            current = 0
            current_episode = episode_id
        if m.get("role") == "client" and is_substantive_client_message(m.get("text")):
            current += 1
            max_streak = max(max_streak, current)
        elif m.get("role") == "employee" and clean_text(m.get("text")):
            current = 0
    return max_streak


def _employee_short_after_complaint(messages: list) -> bool:
    saw_complaint = False
    current_episode = None
    for m in messages:
        episode_id = m.get("episode_id")
        if episode_id != current_episode:
            saw_complaint = False
            current_episode = episode_id
        role = m.get("role")
        text = _norm_for_filter(m.get("text"))
        if not text:
            continue
        if role == "client" and any(phrase in text for phrase in RISK_PHRASES):
            saw_complaint = True
            continue
        if role == "employee" and saw_complaint:
            return len(text) <= 25
    return False


def _ai_filter_decision(conv: dict) -> tuple[bool, str, str]:
    """
    Возвращает (send, candidate_priority, reason).
    P1 здесь означает только приоритет обработки AI-кандидата при лимитах.
    """
    messages = conv.get("messages", [])
    if _is_obviously_normal_short_dialog(messages):
        return False, "P3", "очевидно нормальный короткий диалог"

    client_text = " ".join(
        _norm_for_filter(m.get("text"))
        for m in messages
        if m.get("role") == "client"
    )
    all_text = " ".join(_norm_for_filter(m.get("text")) for m in messages)

    matched = [phrase for phrase in RISK_PHRASES if phrase in client_text]
    if matched:
        priority = "P1" if any(phrase in client_text for phrase in P1_RISK_PHRASES) else "P2"
        return True, priority, f"риск-фразы: {', '.join(matched[:3])}"

    if _max_client_streak(messages) >= 2:
        return True, "P2", "несколько содержательных сообщений клиента подряд"

    if _employee_short_after_complaint(messages):
        return True, "P2", "короткий ответ сотрудника после жалобы"

    negative_markers = ("!", "???", "?!", "ужас", "кошмар", "бесит", "недоволен", "не доволен")
    if any(marker in all_text for marker in negative_markers):
        return True, "P2", "негативная эмоциональная окраска"

    return False, "P3", "нет признаков риска"


def should_send_to_ai(conv: dict) -> bool:
    send, _, _ = _ai_filter_decision(conv)
    return send


def _merge_issues_by_conversation(issues: list) -> list:
    merged: dict = {}
    for issue in issues:
        cid = issue.get("conversation_id")
        if not cid:
            continue
        if cid not in merged:
            merged[cid] = dict(issue)
            merged[cid]["problems"] = list(issue.get("problems", []))
        else:
            merged[cid]["problems"].extend(issue.get("problems", []))
    return list(merged.values())


def analyze_source(conversations: list, chat_type: str, source: str,
                   chat_id: str = "", channel_id: str = "",
                   skip_ai_conversation_keys: set[tuple[str, str]] | None = None,
                   force_ai_scan: bool = False, check_dispatcher_sla: bool = False,
                   sla_check_until=None, no_reply_check_until=None) -> tuple[list, int, dict]:
    """
    Возвращает (issues, total_dialogs_count, analysis_stats).
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
    logger.info(f"После дедупа: {len(deduped)}")

    slow_responses = []
    if check_dispatcher_sla and sla_check_until is not None:
        slow_responses = analyze_dispatcher_response_sla(deduped, sla_check_until)

    all_issues: list = []
    ai_candidates: list = []
    skipped_by_filter = 0
    skipped_already_processed = 0
    return_requests_checked = 0
    return_without_retention_found = 0
    skip_ai_conversation_keys = skip_ai_conversation_keys or set()

    # Боты — не анализируем через AI
    BOT_EMPLOYEES = {"stax система", "stax system"}

    for conv in deduped:
        if not has_client_message(conv["messages"]):
            continue

        emp = (conv.get("employee") or "").strip().lower()
        is_bot = emp in BOT_EMPLOYEES

        no_reply_issue = check_no_reply(conv, check_until=no_reply_check_until)
        if no_reply_issue:
            all_issues.append(no_reply_issue)
        elif has_employee_reply(conv["messages"]) and not is_bot:
            no_greeting_issue = check_no_greeting(conv)
            if no_greeting_issue:
                all_issues.append(no_greeting_issue)

            if has_return_request(conv):
                return_requests_checked += 1
            return_without_retention_issue = check_return_without_retention(conv)
            if return_without_retention_issue:
                return_without_retention_found += 1
                all_issues.append(return_without_retention_issue)

            if force_ai_scan:
                should_send, priority, reason = True, "P1", "полный ручной AI-прогон"
            else:
                should_send, priority, reason = _ai_filter_decision(conv)
            if should_send:
                processed_key = (conv["conversation_id"], conv.get("last_message_key", ""))
                if processed_key in skip_ai_conversation_keys:
                    skipped_already_processed += 1
                    logger.info(f"[AI SKIP DONE] {conv['conversation_id']}: уже обработан AI, новых сообщений нет")
                    continue
                conv["ai_candidate_priority"] = priority
                logger.info(f"[AI CANDIDATE] {conv['conversation_id']} {priority}: {reason}")
                ai_candidates.append(conv)
            else:
                skipped_by_filter += 1
                logger.info(f"[AI SKIP FILTER] {conv['conversation_id']}: {reason}")
        elif is_bot:
            logger.info(f"[SKIP BOT] {conv['conversation_id']}: сотрудник={conv.get('employee')}")

    ai_issues, ai_stats = analyze_with_ai(ai_candidates)
    all_issues.extend(ai_issues)
    all_issues = _merge_issues_by_conversation(all_issues)

    stats = {
        "source_name": chat_type,
        "loaded": len(normalized),
        "after_dedup": len(deduped),
        "sent_to_ai": len(ai_candidates),
        "skipped_by_filter": skipped_by_filter,
        "problems": sum(len(i.get("problems", [])) for i in all_issues),
        "problem_dialogs": len(all_issues),
        "ai_candidates": ai_stats["candidates"],
        "ai_processed": ai_stats["processed"],
        "ai_skipped_low_priority": ai_stats["skipped_low_priority"],
        "ai_errors": ai_stats["errors"],
        "ai_rate_limited": ai_stats["rate_limited"],
        "ai_skipped_already_processed": skipped_already_processed,
        "ai_processed_keys": ai_stats.get("processed_keys", []),
        "return_requests_checked": return_requests_checked,
        "return_without_retention_found": return_without_retention_found,
        "full_ai_scan": force_ai_scan,
        "dispatcher_sla_checked": len(deduped) if check_dispatcher_sla else 0,
        "dispatcher_sla_found": len(slow_responses),
        "slow_responses": slow_responses,
    }

    if source == "wazzup":
        logger.info(f"Wazzup канал: {chat_type}")
        logger.info(f"Выгружено: {stats['loaded']}")
        logger.info(f"После дедупа: {stats['after_dedup']}")
        logger.info(f"Отправлено в AI: {stats['sent_to_ai']}")
        logger.info(f"Пропущено фильтром: {stats['skipped_by_filter']}")
        logger.info(f"Пропущено как уже обработанные AI: {stats['ai_skipped_already_processed']}")
        logger.info(
            "Проверка сдачи без удержания: "
            f"{stats['return_without_retention_found']}/{stats['return_requests_checked']}"
        )
        if check_dispatcher_sla:
            logger.info(f"SLA задержки ответа диспетчера: {stats['dispatcher_sla_found']}")
        logger.info(f"Проблем: {stats['problems']}")
    else:
        logger.info(f"Источник: {chat_type}")
        logger.info(f"Выгружено диалогов: {stats['loaded']}")
        logger.info(f"После дедупа: {stats['after_dedup']}")
        logger.info(f"Отправлено в AI: {stats['sent_to_ai']}")
        logger.info(f"Пропущено фильтром: {stats['skipped_by_filter']}")
        logger.info(f"Пропущено как уже обработанные AI: {stats['ai_skipped_already_processed']}")
        logger.info(
            "Проверка сдачи без удержания: "
            f"{stats['return_without_retention_found']}/{stats['return_requests_checked']}"
        )
        if check_dispatcher_sla:
            logger.info(f"SLA задержки ответа диспетчера: {stats['dispatcher_sla_found']}")
        logger.info(f"Найдено проблем: {stats['problems']}")
        logger.info(f"AI ошибок: {stats['ai_errors']}")

    return all_issues, len(deduped), stats
