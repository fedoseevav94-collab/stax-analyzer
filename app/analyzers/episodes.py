"""
Подготовка сообщений к последовательному анализу.

AI и код должны смотреть на диалог как на хронологию событий, а не как на набор
цитат. Здесь каждому сообщению добавляется стабильный message_index, episode_id
и нормализованный message_id из API.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from app.config import MoscowTZ
from app.utils.text import clean_text

EPISODE_GAP_SECONDS = 6 * 60 * 60


def message_timestamp(message: dict) -> int | None:
    for field in ("created_date", "date", "timestamp", "created_at"):
        value = message.get(field)
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if text.isdigit():
            return int(text)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        return int(parsed.timestamp())
    return None


def _message_id(message: dict) -> str:
    return clean_text(message.get("message_id")) or clean_text(message.get("id"))


def _message_date(ts: int | None):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, MoscowTZ).date()


def _starts_new_episode(prev: dict | None, current: dict) -> bool:
    if not prev:
        return True

    prev_ts = prev.get("created_ts")
    cur_ts = current.get("created_ts")
    if prev_ts is None or cur_ts is None:
        return False

    if _message_date(prev_ts) != _message_date(cur_ts):
        return True
    return abs(cur_ts - prev_ts) > EPISODE_GAP_SECONDS


def prepare_messages(messages: list[dict]) -> list[dict]:
    """
    Возвращает копии сообщений в хронологическом порядке с индексами.

    Если у всех сообщений есть timestamp, сортируем по времени. Если timestamps
    неполные, сохраняем порядок API, чтобы не сломать источник с частичными
    данными.
    """
    prepared = []
    for original_index, message in enumerate(messages or []):
        item = dict(message or {})
        ts = message_timestamp(item)
        if ts is not None:
            item["created_ts"] = ts
        msg_id = _message_id(item)
        if msg_id:
            item["message_id"] = msg_id
        item["_original_index"] = original_index
        prepared.append(item)

    if prepared and all(m.get("created_ts") is not None for m in prepared):
        prepared.sort(key=lambda m: (m["created_ts"], m["_original_index"]))

    episode_id = 0
    prev = None
    for index, message in enumerate(prepared, start=1):
        if _starts_new_episode(prev, message):
            episode_id += 1
        message["message_index"] = index
        message["episode_id"] = episode_id
        message.pop("_original_index", None)
        prev = message

    return prepared


def conversation_last_message_key(messages: list[dict]) -> str:
    meaningful = [
        m for m in messages or []
        if clean_text(m.get("text")) or clean_text(m.get("message_id")) or clean_text(m.get("id"))
    ]
    if not meaningful:
        return ""

    last = meaningful[-1]
    raw = "|".join((
        clean_text(last.get("message_id")) or clean_text(last.get("id")),
        clean_text(last.get("created_ts")) or clean_text(last.get("created_date")) or clean_text(last.get("date")),
        clean_text(last.get("role")),
        clean_text(last.get("text"))[:160],
        str(len(meaningful)),
    ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
