"""
Утилиты для работы с текстом.
"""
import re


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_category(text: str) -> str:
    if not text:
        return ""
    t = str(text).strip().upper().replace("Ё", "Е")
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"[^А-ЯA-Z0-9_]", "", t)
    return t


def normalize_for_quote_match(text: str) -> str:
    if not text:
        return ""
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[^\wа-я]+", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_substantive_client_message(text: str) -> bool:
    """
    True если сообщение клиента содержательное (требует ответа).
    False для скриншотов, бот-команд, коротких закрывающих реплик.
    """
    t = (text or "").strip().lower().replace("ё", "е")
    if not t:
        return False

    service_starts = (
        "screenshot", "photo", "video", "voice", "audio", "document",
        "file", "sticker", "gif", "animation", "location", "contact",
        "[вложение]", "[фото]", "[видео]", "[файл]",
    )
    if t.startswith(service_starts):
        return False

    if t.startswith("/"):
        return False

    if len(t) <= 40:
        t_clean = re.sub(r"[^а-я ]", "", t).strip()
        closing_words = (
            "спасибо", "спс", "благодарю", "взаимно", "ок", "окей", "хорошо",
            "понял", "поняла", "ясно", "угу", "ага", "принято",
            "и вам", "вам тоже", "хорошего дня", "хорошего вечера",
            "доброй ночи", "до свидания", "всего доброго", "всего хорошего",
        )
        if t_clean in closing_words:
            return False
        if any(t_clean.startswith(w + " ") for w in closing_words):
            return False
        if len(t_clean) <= 25 and ("спасибо" in t_clean or "благодарю" in t_clean):
            return False

    return True
