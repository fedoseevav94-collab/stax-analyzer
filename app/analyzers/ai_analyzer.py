"""
AI-анализ диалогов: батчинг, вызов AI, валидация результатов.
"""
import json
import time

from app.ai.prompts import make_batches, build_analysis_prompt, ANALYSIS_SYSTEM
from app.ai.providers import call_ai
from app.analyzers.quote_validator import quote_exists_in_messages, quote_message_indexes
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


def _employee_quote_after_client_quote(employee_quote: str, client_quote: str, messages: list) -> bool:
    client_indexes = quote_message_indexes(client_quote, messages, role="client")
    employee_indexes = quote_message_indexes(employee_quote, messages, role="employee")
    return any(emp_idx > client_idx for client_idx in client_indexes for emp_idx in employee_indexes)


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

    if not emp_quote:
        logger.info(f"[SKIP NO_Q] {conv['conversation_id']} {category}: нет цитаты сотрудника")
        return None
    if not quote_exists_in_messages(emp_quote, messages, role="employee"):
        logger.info(f"[SKIP FAKE_Q] {conv['conversation_id']} {category}: цитата сотр. {emp_quote[:60]!r} не найдена")
        return None
    if client_quote and not quote_exists_in_messages(client_quote, messages, role="client"):
        logger.info(f"[SKIP FAKE_Q] {conv['conversation_id']} {category}: цитата клиента {client_quote[:60]!r} не найдена")
        return None
    if category == "КОНФЛИКТ" and not client_quote:
        logger.info(f"[SKIP NO_CLIENT_Q] {conv['conversation_id']} КОНФЛИКТ: нет цитаты клиента")
        return None
    if category == "КОНФЛИКТ" and not _employee_quote_after_client_quote(emp_quote, client_quote, messages):
        logger.info(f"[SKIP BAD_ORDER] {conv['conversation_id']} КОНФЛИКТ: ответ сотрудника не после жалобы клиента")
        return None
    if category == "КОНФЛИКТ" and not _employee_quote_shows_conflict(emp_quote):
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

    description = f"{reasoning} Цитата сотрудника: «{emp_quote}»"
    if client_quote:
        description += f" Реакция на: «{client_quote}»"
    severity = clean_text(problem.get("severity")) or "средняя"

    return {
        "category": category,
        "description": description,
        "severity": severity,
        "confidence": confidence,
        "employee_quote": emp_quote,
        "client_quote": client_quote,
        "priority": calculate_priority(category, severity, client_quote, description),
    }


def _empty_stats(candidates_count: int) -> dict:
    return {
        "candidates": candidates_count,
        "processed": 0,
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
