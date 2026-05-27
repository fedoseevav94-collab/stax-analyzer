"""
Умное обрезание диалогов.
Вместо text[:N] сохраняем начало + конец, чтобы AI видел контекст.
"""
from app.config import MAX_DIALOG_CHARS

HEAD_CHARS = 2000
TAIL_CHARS = 3500


def smart_truncate(dialog_text: str) -> str:
    """
    Если диалог вмещается — возвращаем целиком.
    Если нет — сохраняем начало (контекст) и конец (финальный конфликт/грубость).
    """
    if len(dialog_text) <= MAX_DIALOG_CHARS:
        return dialog_text

    head = dialog_text[:HEAD_CHARS]
    tail = dialog_text[-TAIL_CHARS:]
    return head + "\n\n[...диалог обрезан — показаны начало и конец...]\n\n" + tail
