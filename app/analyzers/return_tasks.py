"""
Проверка удержания по карточкам из чата задач сдачи.

Карточка сдачи сама по себе не является проблемой. Мы ставим проблему только
когда карточка сопоставлена с диспетчерским диалогом и в диалоге виден сценарий:
сотрудник записывает на сдачу, но не спрашивает причину и не предлагает решение.
"""
from __future__ import annotations

import re

from app.analyzers.deterministic import (
    RETENTION_OR_CLARIFICATION_MARKERS,
    SCHEDULING_ONLY_MARKERS,
    _has_any,
    _issue,
    _message_id,
    _message_index,
    _problem,
    check_return_without_retention,
)
from app.exporters.telegram_return_tasks import normalize_plate
from app.logger import logger
from app.utils.text import clean_text


def _norm_text(value: str) -> str:
    text = clean_text(value).lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9 ]+", " ", text).strip()


def _name_tokens(full_name: str) -> list[str]:
    return [
        token for token in _norm_text(full_name).split()
        if len(token) >= 3
    ]


def _flatten_scalars(value) -> list[str]:
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(_flatten_scalars(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_flatten_scalars(item))
        return result
    if isinstance(value, (str, int, float)):
        return [clean_text(value)]
    return []


def _conversation_haystack(conv: dict) -> str:
    parts = _flatten_scalars(conv)
    return _norm_text(" ".join(parts))


def _conversation_plates(conv: dict) -> set[str]:
    plates = set()
    for raw in _flatten_scalars(conv):
        normalized = normalize_plate(raw)
        for match in re.finditer(r"[A-Z]\d{3}[A-Z]{2}\d{2,3}", normalized):
            plates.add(match.group(0))
    return plates


def _match_score(task: dict, conv: dict) -> int:
    score = 0
    task_plate = clean_text(task.get("plate"))
    conv_plates = _conversation_plates(conv)
    if task_plate and task_plate in conv_plates:
        score += 8

    haystack = _conversation_haystack(conv)
    name_tokens = _name_tokens(task.get("full_name"))
    if len(name_tokens) >= 2 and all(token in haystack for token in name_tokens[:2]):
        score += 6
    elif name_tokens and name_tokens[0] in haystack:
        score += 3

    return score


def find_matching_conversation(task: dict, conversations: list[dict]) -> dict | None:
    scored = []
    for conv in conversations or []:
        score = _match_score(task, conv)
        if score:
            scored.append((score, conv))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_conv = scored[0]
    if best_score < 6:
        return None
    return best_conv


def _first_scheduling_only_reply(conv: dict) -> dict | None:
    for message in conv.get("messages", []):
        if message.get("role") != "employee":
            continue
        text = clean_text(message.get("text"))
        if not text:
            continue
        if _has_any(text, SCHEDULING_ONLY_MARKERS) and not _has_any(text, RETENTION_OR_CLARIFICATION_MARKERS):
            return message
    return None


def _has_retention_attempt(conv: dict) -> bool:
    for message in conv.get("messages", []):
        if message.get("role") == "employee" and _has_any(message.get("text"), RETENTION_OR_CLARIFICATION_MARKERS):
            return True
    return False


def _task_client_quote(task: dict) -> str:
    parts = []
    if task.get("full_name"):
        parts.append(f"Водитель: {task['full_name']}")
    if task.get("return_reason"):
        parts.append(f"Причина сдачи: {task['return_reason']}")
    if task.get("appointment_date"):
        parts.append(f"Дата записи: {task['appointment_date']}")
    return "; ".join(parts) or clean_text(task.get("raw_text"))[:180]


def _build_task_issue(conv: dict, task: dict, employee_reply: dict) -> dict:
    employee_quote = clean_text(employee_reply.get("text"))
    full_name = clean_text(task.get("full_name")) or "водитель"
    reason = clean_text(task.get("return_reason"))
    reason_part = f" Причина из карточки: {reason}." if reason else ""
    issue = _issue(conv, [_problem(
        category="СДАЧА_БЕЗ_УДЕРЖАНИЯ",
        description=(
            f"По карточке сдачи {full_name} найден диспетчерский диалог. "
            f"Сотрудник уточнил только запись на сдачу и не задал вопрос о причине "
            f"или вариантах удержания.{reason_part}"
        ),
        severity="средняя",
        priority="P2",
        client_quote=_task_client_quote(task),
        employee_quote=employee_quote,
        message_id=_message_id(employee_reply),
        employee_message_index=_message_index(employee_reply),
    )])
    issue["return_task"] = task
    return issue


def analyze_return_tasks_for_conversations(
    tasks: list[dict],
    conversations: list[dict],
    existing_return_issue_ids: set[str] | None = None,
) -> tuple[list[dict], dict]:
    existing_return_issue_ids = existing_return_issue_ids or set()
    issues = []
    matched = 0
    unmatched = 0

    for task in tasks or []:
        conv = find_matching_conversation(task, conversations)
        if not conv:
            unmatched += 1
            logger.info(
                "[RETURN TASKS] Не найден диспетчерский диалог: "
                f"{task.get('full_name') or task.get('car')}"
            )
            continue

        matched += 1
        conv_id = clean_text(conv.get("conversation_id"))
        if conv_id in existing_return_issue_ids:
            logger.info(f"[RETURN TASKS] {conv_id}: проблема сдачи уже найдена обычной проверкой")
            continue

        direct_issue = check_return_without_retention(conv)
        if direct_issue:
            direct_issue["return_task"] = task
            issues.append(direct_issue)
            existing_return_issue_ids.add(conv_id)
            continue

        if _has_retention_attempt(conv):
            logger.info(f"[RETURN TASKS] {conv_id}: найдена попытка удержания/уточнения")
            continue

        scheduling_reply = _first_scheduling_only_reply(conv)
        if scheduling_reply:
            issues.append(_build_task_issue(conv, task, scheduling_reply))
            existing_return_issue_ids.add(conv_id)
            continue

        logger.info(f"[RETURN TASKS] {conv_id}: нет доказанного нарушения удержания")

    stats = {
        "return_task_cards_loaded": len(tasks or []),
        "return_task_cards_matched": matched,
        "return_task_cards_unmatched": unmatched,
        "return_task_retention_found": len(issues),
    }
    return issues, stats
