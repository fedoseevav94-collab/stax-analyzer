"""
AI-анализ диалогов: батчинг, вызов AI, валидация результатов.
"""
import json
import time

from app.ai.prompts import make_batches, build_analysis_prompt, ANALYSIS_SYSTEM
from app.ai.providers import call_ai
from app.analyzers.quote_validator import quote_exists_in_messages
from app.config import CONFIDENCE_THRESHOLD, AI_BATCH_DELAY_SECONDS
from app.logger import logger
from app.utils.text import clean_text, normalize_category


def _extract_json(raw: str) -> dict:
    raw = (raw or "").strip().replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"AI не вернул JSON. Ответ: {raw[:500]}")
    return json.loads(raw[start:end])


def _validate_problem(problem: dict, conv: dict) -> dict | None:
    """
    Финальная валидация AI-проблемы:
    1. Категория допустимая.
    2. Confidence >= порог.
    3. employee_quote реально есть в диалоге дословно.
    4. client_quote если есть — тоже реально есть.
    """
    category = normalize_category(problem.get("category"))
    if category not in {"ГРУБОСТЬ", "НЕКОМПЕТЕНТНОСТЬ", "КОНФЛИКТ", "БЕЗ_ПРИВЕТСТВИЯ"}:
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
    if category == "БЕЗ_ПРИВЕТСТВИЯ" and not emp_quote:
        logger.info(f"[SKIP NO_Q] {conv['conversation_id']} БЕЗ_ПРИВЕТСТВИЯ: нет цитаты сотрудника")
        return None

    reasoning = clean_text(problem.get("reasoning")) or "—"
    description = f"{reasoning} Цитата сотрудника: «{emp_quote}»"
    if client_quote:
        description += f" Реакция на: «{client_quote}»"

    return {
        "category": category,
        "description": description,
        "severity": clean_text(problem.get("severity")) or "средняя",
        "confidence": confidence,
        "employee_quote": emp_quote,
        "client_quote": client_quote,
    }


def analyze_with_ai(candidates: list) -> list:
    """
    Принимает список нормализованных диалогов (уже прошедших детерминированные проверки).
    Возвращает список issues с проблемами.
    """
    if not candidates:
        return []

    batches = make_batches(candidates)
    logger.info(f"AI-анализ: {len(candidates)} диалогов, пачек: {len(batches)}")

    conv_by_id = {c["conversation_id"]: c for c in candidates}
    all_issues = []
    current_delay = AI_BATCH_DELAY_SECONDS

    for idx, batch in enumerate(batches, start=1):
        logger.info(f"Пачка {idx}/{len(batches)} ({len(batch)} диалогов)...")
        hit_rate_limit = False
        try:
            raw = call_ai(ANALYSIS_SYSTEM, build_analysis_prompt(batch))
            parsed = _extract_json(raw)

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
                        "problems": validated,
                    })

            current_delay = max(AI_BATCH_DELAY_SECONDS, int(current_delay * 0.8))

        except Exception as e:
            logger.error(f"[ОШИБКА пачки {idx}] {type(e).__name__}: {e}")
            if "429" in str(e) or "RATE_LIMIT" in str(e):
                hit_rate_limit = True

        if hit_rate_limit:
            current_delay = min(60, current_delay * 2)
            logger.info(f"Адаптивная пауза увеличена до {current_delay} сек")

        time.sleep(current_delay)

    return all_issues
