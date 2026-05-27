"""
Утилиты для хеширования.
"""
import hashlib
import re


def hash_description(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())[:100]
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
