"""
AI-анализ диалогов: батчинг, вызов AI, валидация результатов.
"""
import json
import time

from app.ai.prompts import make_batches, build_analysis_prompt, ANALYSIS_SYSTEM
from app.ai.providers import call_ai
from app.analyzers.quote_validator import quote_exists_in_message
from app.config import CONFIDENCE_THRESHOLD, AI_BATCH_DELAY_SECONDS
from app.logger import logger
from app.utils.text import clean_text, normalize_category

ALLOWED_AI_CATEGORIES = {
    "ГРУБОСТЬ",
    "НЕКОМПЕТЕНТНОСТЬ",
    "КОНФЛИКТ",
}


def calculate_priority(category: str, severity: str, client_quote: str = "", description: str = "") -> str:
    category = normalize_category(category)
    sev = (severity or "").strip().lower()

    if sev == "низкая":
        return "P3"
    if category in {"БЕЗ_ОТВЕТА", "ГРУБОСТЬ"}:
        return "P1"
    if category == "КОНФЛИКТ" and sev == "высокая":
        return "P1"
    if category == "НЕКОМПЕТЕНТНОСТЬ":
        return "P2"
    if category == "КОНФЛИКТ" and sev == "средняя":
        return "P2"
    return "P3"


def _extract_json(raw: str) -> dict:
    raw = (raw or "").strip().replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"AI не вернул JSON. Ответ: {raw[:500]}")
    return json.loads(raw[start:end])


def _message_ts(message: dict) -> int | None:
    if message.get("created_ts") is not None:
        try:
            return int(message.get("created_ts"))
        except (TypeError, ValueError):
            pass

    for field in ("created_date", "date", "timestamp", "created_at"):
        value = message.get(field)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _problem_message_index(problem: dict, field: str) -> int | None:
    value = problem.get(field)
    if value in (None, ""):
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def _message_by_index(messages: list, public_index: int | None) -> dict | None:
    if public_index is None:
        return None
    for message in messages:
        try:
            if int(message.get("message_index")) == public_index:
                return message
        except (TypeError, ValueError):
            continue
    return None


def _message_id(message: dict | None) -> str:
    if not message:
        return ""
    return clean_text(message.get("message_id")) or clean_text(message.get("id"))


def _same_episode(left: dict | None, right: dict | None) -> bool:
    if not left or not right:
        return False
    left_episode = left.get("episode_id")
    right_episode = right.get("episode_id")
    if left_episode in (None, "") or right_episode in (None, ""):
        return True
    return str(left_episode) == str(right_episode)


def _messages_are_close(employee_message: dict, client_message: dict,
                        max_seconds: int = 6 * 60 * 60, max_index_gap: int = 6) -> bool:
    employee_ts = _message_ts(employee_message)
    client_ts = _message_ts(client_message)
    if employee_ts is not None and client_ts is not None:
        return abs(employee_ts - client_ts) <= max_seconds
    try:
        employee_index = int(employee_message.get("message_index"))
        client_index = int(client_message.get("message_index"))
    except (TypeError, ValueError):
        return False
    return abs(employee_index - client_index) <= max_index_gap


def _employee_quote_shows_conflict(employee_quote: str) -> bool:
    text = clean_text(employee_quote).lower().replace("ё", "е")
    helpful_markers = (
        "можете", "можно", "попробуйте", "по ссылке", "ссылка",
        "напишите", "пришлите", "скиньте", "уточним", "передам",
        "свяжемся", "ждем", "ждём", "предлага", "альтернатив",
    )
    hard_conflict_markers = (
        "сами", "ваша вина", "вы винов", "виноваты", "ваши проблемы",
        "не пишите", "не звоните", "жалуйтесь", "как хотите",
        "это ваши", "надо было", "мы не виноваты",
    )
    if any(marker in text for marker in hard_conflict_markers):
        return True

    if any(marker in text for marker in helpful_markers):
        return False

    refusal_markers = (
        "не можем", "не сможем", "не будем", "не обязаны", "не входит",
        "отказ", "отказыва", "невозможно", "не получится", "ничем не",
        "закрываем", "закрыт", "больше не",
    )
    if any(marker in text for marker in refusal_markers):
        return True

    # Вопросы и уточнения сами по себе не доказывают конфликт.
    if "?" in text:
        return False

    return False


def _client_quote_is_external_claim(client_quote: str) -> bool:
    """
    Клиентская фраза со ссылкой на внешние источники не доказывает ошибку сотрудника.
    Для НЕКОМПЕТЕНТНОСТЬ нужна опора внутри самого диалога, а не "где-то написано".
    """
    text = clean_text(client_quote).lower().replace("ё", "е")
    external_markers = (
        "в интернете", "на сайте", "на вашем сайте", "у вас написано",
        "где-то написано", "где то написано", "написано что", "прочитал",
        "прочитала", "читал", "читала", "нашел", "нашла", "гугл",
        "яндекс", "мне сказали", "сказали что", "говорили что",
        "вроде написано", "кажется написано", "по информации",
    )
    return any(marker in text for marker in external_markers)


def _employee_quote_is_technical_dismissal(employee_quote: str) -> bool:
    """
    В STAX "уволить водителя" в переписке диспетчеров означает техническое
    действие в программе, а не угрозу клиенту/водителю.
    """
    text = clean_text(employee_quote).lower().replace("ё", "е")
    dismissal_markers = (
        "уволить", "уволим", "уволили", "уволь", "уволен",
        "уволена", "увольнение", "увольнения",
    )
    return any(marker in text for marker in dismissal_markers)


def _validate_problem(problem: dict, conv: dict) -> dict | None:
    """
    Финальная валидация AI-проблемы:
    1. Категория допустимая.
    2. Confidence >= порог.
    3. employee_quote реально есть в диалоге дословно.
    4. client_quote если есть — тоже реально есть.
    """
    category = normalize_category(problem.get("category"))
    if category not in ALLOWED_AI_CATEGORIES:
        logger.info(f"[SKIP CAT] {conv['conversation_id']}: неизвестная категория {category!r}")
        return None

    confidence = float(problem.get("confidence") or 0.0)
    if confidence < CONFIDENCE_THRESHOLD:
        logger.info(f"[SKIP CONF] {conv['conversation_id']} {category}: {confidence:.2f} < {CONFIDENCE_THRESHOLD}")
        return None

    messages = conv.get("messages", [])
    emp_quote = clean_text(problem.get("employee_quote"))
    client_quote = clean_text(problem.get("client_quote"))
    employee_index = _problem_message_index(problem, "employee_message_index")
    client_index = _problem_message_index(problem, "client_message_index")

    if not emp_quote:
        logger.info(f"[SKIP NO_Q] {conv['conversation_id']} {category}: нет цитаты сотрудника")
        return None
    if employee_index is None:
        logger.info(f"[SKIP NO_INDEX] {conv['conversation_id']} {category}: нет employee_message_index")
        return None

    employee_message = _message_by_index(messages, employee_index)
    if not employee_message or not quote_exists_in_message(emp_quote, employee_message, role="employee"):
        logger.info(
            f"[SKIP BAD_INDEX] {conv['conversation_id']} {category}: "
            f"employee_quote не найден в #{employee_index}"
        )
        return None

    if category in {"ГРУБОСТЬ", "КОНФЛИКТ"} and _employee_quote_is_technical_dismissal(emp_quote):
        logger.info(
            f"[SKIP TECH_DISMISSAL] {conv['conversation_id']} {category}: "
            "увольнение в STAX трактуется как техническое действие в программе"
        )
        return None

    client_message = None
    if client_quote:
        if client_index is None:
            logger.info(f"[SKIP NO_INDEX] {conv['conversation_id']} {category}: нет client_message_index")
            return None
        client_message = _message_by_index(messages, client_index)
        if not client_message or not quote_exists_in_message(client_quote, client_message, role="client"):
            logger.info(
                f"[SKIP BAD_INDEX] {conv['conversation_id']} {category}: "
                f"client_quote не найден в #{client_index}"
            )
            return None
    elif client_index is not None:
        client_message = _message_by_index(messages, client_index)

    if category == "КОНФЛИКТ" and not client_quote:
        logger.info(f"[SKIP NO_CLIENT_Q] {conv['conversation_id']} КОНФЛИКТ: нет цитаты клиента")
        return None
    if category == "КОНФЛИКТ":
        if employee_index <= client_index:
            logger.info(f"[SKIP BAD_ORDER] {conv['conversation_id']} КОНФЛИКТ: ответ сотрудника не после жалобы клиента")
            return None
        if not _same_episode(employee_message, client_message):
            logger.info(f"[SKIP DIFF_EPISODE] {conv['conversation_id']} КОНФЛИКТ: цитаты из разных эпизодов")
            return None
        if not _employee_quote_shows_conflict(emp_quote):
            logger.info(f"[SKIP NEUTRAL_REPLY] {conv['conversation_id']} КОНФЛИКТ: цитата сотрудника не доказывает конфликт")
            return None

    reasoning = clean_text(problem.get("reasoning")) or "—"
    if category == "НЕКОМПЕТЕНТНОСТЬ":
        if not client_quote:
            logger.info(f"[SKIP NO_CLIENT_Q] {conv['conversation_id']} НЕКОМПЕТЕНТНОСТЬ: нет опорной цитаты")
            return None
        reasoning_norm = reasoning.lower().replace("ё", "е")
        contradiction_markers = (
            "противореч", "опровер", "ложн", "неверн", "расхожд",
            "сначала", "потом", "другая информация", "в диалоге указано",
        )
        if not any(marker in reasoning_norm for marker in contradiction_markers):
            logger.info(f"[SKIP WEAK_REASON] {conv['conversation_id']} НЕКОМПЕТЕНТНОСТЬ: нет явного противоречия")
            return None
        if _client_quote_is_external_claim(client_quote):
            logger.info(
                f"[SKIP EXTERNAL_CLAIM] {conv['conversation_id']} "
                "НЕКОМПЕТЕНТНОСТЬ: клиентская цитата ссылается на внешний источник"
            )
            return None
        if employee_index == client_index or not _same_episode(employee_message, client_message):
            logger.info(f"[SKIP DIFF_EPISODE] {conv['conversation_id']} НЕКОМПЕТЕНТНОСТЬ: цитаты из разных эпизодов")
            return None
        if not _messages_are_close(employee_message, client_message):
            logger.info(f"[SKIP FAR_QUOTES] {conv['conversation_id']} НЕКОМПЕТЕНТНОСТЬ: цитаты из разных частей диалога")
            return None

    description = f"{reasoning} Цитата сотрудника: «{emp_quote}»"
    if client_quote:
        description += f" Реакция на: «{client_quote}»"
    severity = clean_text(problem.get("severity")) or "средняя"
    message_id = _message_id(employee_message) or _message_id(client_message)

    validated = {
        "category": category,
        "description": description,
        "severity": severity,
        "confidence": confidence,
        "employee_quote": emp_quote,
        "client_quote": client_quote,
        "employee_message_index": employee_index,
        "priority": calculate_priority(category, severity, client_quote, description),
    }
    if client_index is not None:
        validated["client_message_index"] = client_index
    if message_id:
        validated["message_id"] = message_id
    return validated


def _empty_stats(candidates_count: int) -> dict:
    return {
        "candidates": candidates_count,
        "processed": 0,
        "processed_keys": [],
        "skipped_low_priority": 0,
        "errors": 0,
        "rate_limited": False,
    }


def analyze_with_ai(candidates: list) -> tuple[list, dict]:
    """
    Принимает список нормализованных диалогов (уже прошедших детерминированные проверки).
    Возвращает (issues, stats).
    """
    stats = _empty_stats(len(candidates))
    if not candidates:
        return [], stats

    batches = make_batches(candidates)
    logger.info(f"AI-анализ: {len(candidates)} диалогов, пачек: {len(batches)}")

    conv_by_id = {c["conversation_id"]: c for c in candidates}
    all_issues = []
    current_delay = AI_BATCH_DELAY_SECONDS
    rate_limited_mode = False

    for idx, batch in enumerate(batches, start=1):
        if rate_limited_mode:
            p1_batch = [c for c in batch if c.get("ai_candidate_priority") == "P1"]
            skipped = len(batch) - len(p1_batch)
            if skipped:
                stats["skipped_low_priority"] += skipped
                logger.info(f"[AI RATE LIMIT] Пачка {idx}: пропущено P2-кандидатов: {skipped}")
            if not p1_batch:
                continue
            batch = p1_batch

        logger.info(f"Пачка {idx}/{len(batches)} ({len(batch)} диалогов)...")
        hit_rate_limit = False
        try:
            raw = call_ai(ANALYSIS_SYSTEM, build_analysis_prompt(batch))
            parsed = _extract_json(raw)
            stats["processed"] += len(batch)
            stats["processed_keys"].extend(
                {
                    "conversation_id": c["conversation_id"],
                    "last_message_key": c.get("last_message_key", ""),
                }
                for c in batch
            )

            for item in parsed.get("issues", []) or []:
                cid = str(item.get("conversation_id", "")).strip()
                raw_problems = item.get("problems", []) or []
                if not cid or not raw_problems or cid not in conv_by_id:
                    continue

                base = conv_by_id[cid]
                validated = [p for p in (_validate_problem(rp, base) for rp in raw_problems) if p]
                if validated:
                    all_issues.append({
                        "conversation_id": cid,
                        "employee": base["employee"],
                        "date": base.get("date", ""),
                        "chat_type": base["chat_type"],
                        "dialog_link": base["dialog_link"],
                        "first_client_msg": base["first_client_msg"],
                        "topic": base.get("topic", "Другое"),
                        "source": base.get("source", ""),
                        "chat_id": base.get("chat_id", ""),
                        "channel_id": base.get("channel_id", ""),
                        "problems": validated,
                    })

            current_delay = max(AI_BATCH_DELAY_SECONDS, int(current_delay * 0.8))

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"[ОШИБКА пачки {idx}] {type(e).__name__}: {e}")
            if "429" in str(e) or "RATE_LIMIT" in str(e):
                hit_rate_limit = True

        if hit_rate_limit:
            stats["rate_limited"] = True
            rate_limited_mode = True
            current_delay = min(60, current_delay * 2)
            logger.info(f"Адаптивная пауза увеличена до {current_delay} сек")
            logger.info("AI rate limit: дальше обрабатываются только P1-кандидаты")

        time.sleep(current_delay)

    return all_issues, stats
