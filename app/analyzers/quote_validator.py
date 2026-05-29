"""
Валидация цитат — главная защита от выдумок AI.
"""
from app.utils.text import normalize_for_quote_match


def _quote_match_params(quote: str) -> tuple[str, int] | tuple[None, None]:
    quote_norm = normalize_for_quote_match(quote)
    if len(quote_norm) < 5:
        return None, None

    if len(quote_norm) <= 20:
        min_match_len = len(quote_norm)
    elif len(quote_norm) <= 50:
        min_match_len = int(len(quote_norm) * 0.85)
    else:
        min_match_len = int(len(quote_norm) * 0.70)
    return quote_norm, min_match_len


def _quote_matches_message(quote_norm: str, min_match_len: int, message_text: str) -> bool:
    msg_norm = normalize_for_quote_match(message_text)
    if quote_norm in msg_norm:
        return True
    if min_match_len < len(quote_norm):
        for i in range(len(quote_norm) - min_match_len + 1):
            if quote_norm[i:i + min_match_len] in msg_norm:
                return True
    return False


def quote_message_indexes(quote: str, messages: list, role: str = None) -> list[int]:
    if not quote or not messages:
        return []

    quote_norm, min_match_len = _quote_match_params(quote)
    if not quote_norm:
        return []

    indexes = []
    for index, m in enumerate(messages):
        if role and m.get("role") != role:
            continue
        msg_text = str(m.get("text") or "").strip()
        if msg_text and _quote_matches_message(quote_norm, min_match_len, msg_text):
            indexes.append(index)
    return indexes


def quote_exists_in_messages(quote: str, messages: list, role: str = None) -> bool:
    """
    True если цитата существует дословно в одном из сообщений диалога.
    Для длинных цитат разрешено вхождение 70% подстроки.
    Для коротких (<=20 символов) требуется точное вхождение.
    Цитаты короче 5 символов не валидируем.
    """
    if not quote or not messages:
        return False

    return bool(quote_message_indexes(quote, messages, role=role))


def quote_exists_in_message(quote: str, message: dict, role: str = None) -> bool:
    """True если цитата относится к конкретному сообщению."""
    if not quote or not message:
        return False
    if role and message.get("role") != role:
        return False
    return bool(quote_message_indexes(quote, [message], role=role))
