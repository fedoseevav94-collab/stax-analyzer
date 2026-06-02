"""
Выгрузка карточек из Telegram-чата задач сдачи.

Bot API не умеет читать историю чата произвольно, поэтому используем pending
updates и сохраняем offset в БД. Это работает для новых карточек после добавления
бота в чат.
"""
from __future__ import annotations

import re
from datetime import datetime

import requests

from app.config import MoscowTZ
from app.logger import logger
from app.utils.text import clean_text

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

_CYR_TO_LAT = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "а": "A", "в": "B", "е": "E", "к": "K", "м": "M", "н": "H",
    "о": "O", "р": "P", "с": "C", "т": "T", "у": "Y", "х": "X",
})


def normalize_plate(value: str) -> str:
    text = clean_text(value).translate(_CYR_TO_LAT).upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def _extract_plate(value: str) -> str:
    text = clean_text(value).translate(_CYR_TO_LAT).upper()
    for match in re.finditer(r"[A-ZА-Я]\s*\d{3}\s*[A-ZА-Я]{2}\s*\d{2,3}", text):
        plate = normalize_plate(match.group(0))
        if plate:
            return plate
    tokens = [normalize_plate(token) for token in text.split()]
    for token in tokens:
        if re.fullmatch(r"[A-Z]\d{3}[A-Z]{2}\d{2,3}", token):
            return token
    return ""


def _message_from_update(update: dict) -> dict | None:
    return update.get("message") or update.get("channel_post")


def _message_text(message: dict) -> str:
    return str(message.get("text") or message.get("caption") or "").strip()


def _message_datetime(message: dict) -> datetime | None:
    timestamp = message.get("date")
    if timestamp in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(timestamp), MoscowTZ)
    except (TypeError, ValueError):
        return None


def _field_map(text: str) -> dict[str, str]:
    fields = {}
    for raw_label, raw_value in re.findall(r"(?m)^[ \t]*([^:\n]+):[ \t]*(.*)$", text or ""):
        label = clean_text(raw_label).lower().replace("ё", "е")
        value = clean_text(raw_value)
        if not label:
            continue
        if label.startswith("фио"):
            fields["full_name"] = value
        elif label.startswith("категория"):
            fields["driver_category"] = value
        elif label.startswith("дата записи"):
            fields["appointment_date"] = value
        elif label.startswith("комментарий"):
            fields["return_reason"] = value
        elif label.startswith("ответственный"):
            fields["responsible"] = value
        elif label.startswith("машина"):
            fields["car"] = value
        elif label.startswith("период аренды"):
            fields["rental_period"] = value
        elif label.startswith("дата начала работы"):
            fields["park_start_date"] = value
        elif label.startswith("задача менеджеру"):
            fields["manager_task"] = value
    return fields


def parse_return_task_message(message: dict, update_id: int | None = None) -> dict | None:
    text = _message_text(message)
    normalized = text.lower().replace("ё", "е")
    if "запись на возврат авто" not in normalized and "запись на сдачу" not in normalized:
        return None

    fields = _field_map(text)
    full_name = clean_text(fields.get("full_name"))
    car = clean_text(fields.get("car"))
    if not full_name and not car:
        return None

    chat = message.get("chat") or {}
    message_id = clean_text(message.get("message_id"))
    chat_id = clean_text(chat.get("id"))
    return {
        "update_id": update_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "message_datetime": _message_datetime(message),
        "full_name": full_name,
        "driver_category": clean_text(fields.get("driver_category")),
        "appointment_date": clean_text(fields.get("appointment_date")),
        "return_reason": clean_text(fields.get("return_reason")),
        "responsible": clean_text(fields.get("responsible")),
        "car": car,
        "plate": _extract_plate(car),
        "rental_period": clean_text(fields.get("rental_period")),
        "park_start_date": clean_text(fields.get("park_start_date")),
        "manager_task": clean_text(fields.get("manager_task")),
        "raw_text": text,
    }


def _call_telegram(token: str, method: str, payload: dict) -> list[dict]:
    response = requests.post(
        TELEGRAM_API.format(token=token, method=method),
        json=payload,
        timeout=20,
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(clean_text(data.get("description")) or "Telegram API error")
    return data.get("result") or []


def fetch_return_tasks_from_updates(
    token: str,
    chat_id: str,
    offset: int | None,
    limit: int,
    period: dict,
) -> tuple[list[dict], int | None, dict]:
    """
    Возвращает (tasks, next_offset, stats). next_offset можно сохранять только
    после успешной обработки, чтобы при падении workflow не потерять карточки.
    """
    if not token or not chat_id:
        return [], None, {"return_task_fetch_error": 0, "return_task_updates_seen": 0}

    payload = {
        "limit": max(1, min(int(limit or 100), 100)),
        "timeout": 1,
        "allowed_updates": ["message", "channel_post"],
    }
    if offset is not None:
        payload["offset"] = int(offset)

    try:
        updates = _call_telegram(token, "getUpdates", payload)
    except Exception as exc:
        logger.warning(f"[RETURN TASKS] Не удалось прочитать Telegram updates: {exc}")
        return [], None, {"return_task_fetch_error": 1, "return_task_updates_seen": 0}

    latest_update_id = None
    tasks = []
    start_msk = period.get("report_start_msk")
    end_msk = period.get("report_end_msk")

    for update in updates:
        try:
            update_id = int(update.get("update_id"))
        except (TypeError, ValueError):
            update_id = None
        if update_id is not None:
            latest_update_id = max(latest_update_id or update_id, update_id)

        message = _message_from_update(update)
        if not message:
            continue
        chat = message.get("chat") or {}
        if clean_text(chat.get("id")) != clean_text(chat_id):
            continue

        msg_dt = _message_datetime(message)
        if msg_dt and start_msk and end_msk and not (start_msk <= msg_dt <= end_msk):
            continue

        task = parse_return_task_message(message, update_id=update_id)
        if task:
            tasks.append(task)

    next_offset = latest_update_id + 1 if latest_update_id is not None else None
    logger.info(
        f"[RETURN TASKS] updates={len(updates)}, cards={len(tasks)}, "
        f"next_offset={next_offset or ''}"
    )
    return tasks, next_offset, {
        "return_task_fetch_error": 0,
        "return_task_updates_seen": len(updates),
    }
