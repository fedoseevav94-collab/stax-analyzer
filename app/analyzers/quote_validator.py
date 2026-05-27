"""
Валидация цитат — главная защита от выдумок AI.
"""
from app.utils.text import normalize_for_quote_match


def quote_exists_in_messages(quote: str, messages: list, role: str = None) -> bool:
    """
    True если цитата существует дословно в одном из сообщений диалога.
    Для длинных цитат разрешено вхождение 70% подстроки.
    Для коротких (<=20 символов) требуется точное вхождение.
    Цитаты короче 5 символов не валидируем.
    """
    if not quote or not messages:
        return False

    quote_norm = normalize_for_quote_match(quote)
    if len(quote_norm) < 5:
        return False

    if len(quote_norm) <= 20:
        min_match_len = len(quote_norm)
    elif len(quote_norm) <= 50:
        min_match_len = int(len(quote_norm) * 0.85)
    else:
        min_match_len = int(len(quote_norm) * 0.70)

    for m in messages:
        if role and m.get("role") != role:
            continue
        msg_text = str(m.get("text") or "").strip()
        if not msg_text:
            continue
        msg_norm = normalize_for_quote_match(msg_text)
        if quote_norm in msg_norm:
            return True
        if min_match_len < len(quote_norm):
            for i in range(len(quote_norm) - min_match_len + 1):
                if quote_norm[i:i + min_match_len] in msg_norm:
                    return True
    return False
