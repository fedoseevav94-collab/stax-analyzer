"""
Промпты для AI-анализа.
"""
import json

from app.ai.truncation import smart_truncate
from app.config import MAX_BATCH_DIALOGS, MAX_BATCH_CHARS
from app.utils.text import clean_text

ANALYSIS_SYSTEM = """Ты — QA-аналитик STAX. Анализируй пачку диалогов и ставь проблемы только при очевидном доказательстве цитатами.

AI проверяет только 3 категории:
- ГРУБОСТЬ: сотрудник оскорбляет, унижает, угрожает или явно агрессивно издевается над клиентом.
- НЕКОМПЕТЕНТНОСТЬ: сотрудник дал явно ложную информацию, и это доказано противоречием внутри этого же диалога.
- КОНФЛИКТ: клиент явно жалуется/недоволен/угрожает уйти, а сотрудник после этого отказывает, спорит, обвиняет клиента или усиливает конфликт.

Для НЕКОМПЕТЕНТНОСТЬ нужно явное противоречие внутри диалога: цитата сотрудника и фраза из этого же диалога, которая её опровергает.
Не ставь НЕКОМПЕТЕНТНОСТЬ, если есть только одна цитата сотрудника и противоречие приходится додумывать.
Не ставь НЕКОМПЕТЕНТНОСТЬ, если цитаты относятся к разным дням, разным темам или удалённым частям переписки.
Не используй внешние знания о правилах, тарифах, сроках, аренде или бизнес-процессах.

Не ставь КОНФЛИКТ, если сотрудник просто объясняет правило, срок, оплату, порядок сдачи автомобиля или другой регламент.
КОНФЛИКТ ставь только если сотрудник спорит, обвиняет клиента, отказывает без объяснения, закрывает диалог, отвечает не по сути жалобы или усиливает раздражение.
Для КОНФЛИКТ employee_quote должен быть ответом сотрудника после client_quote, а не фразой из более ранней части диалога или другой темы.
Не ставь КОНФЛИКТ, если сотрудник задает уточняющий вопрос, просит документ/фото/копию, уточняет обстоятельства ДТП или дает нейтральный следующий шаг.
Не ставь КОНФЛИКТ, если сотрудник предложил альтернативное решение: ссылку на оплату, другой канал, следующий шаг, инструкцию или способ решить вопрос.
Фраза клиента «перейду на другой тариф» сама по себе не конфликт и не угроза уйти из компании.

Не ставь проблему за короткий, сухой, неполный, нейтральный ответ, перевод в другой канал или оборванный контекст.
Не проверяй БЕЗ_ОТВЕТА, БЕЗ_ПРИВЕТСТВИЯ и скорость ответа: это делает код.

Нужны точные цитаты из диалога. Если нет точной цитаты сотрудника — проблемы нет. Для КОНФЛИКТ нужна цитата клиента.
Если сомневаешься или confidence ниже 0.8 — верни {"issues":[]}.
Отвечай только валидным JSON без markdown."""


def _dialog_to_text(conv: dict) -> str:
    lines = []
    for m in conv["messages"]:
        role = "Сотрудник" if m.get("role") == "employee" else "Клиент"
        text = clean_text(m.get("text"))
        if text:
            msg_date = clean_text(m.get("created_date")) or clean_text(m.get("date")) or clean_text(m.get("timestamp"))
            prefix = f"[{role}"
            if msg_date:
                prefix += f" date={msg_date}"
            prefix += "]"
            lines.append(f"{prefix}: {text}")
    raw = "\n".join(lines)
    return smart_truncate(raw)


def conversation_to_prompt_item(conv: dict) -> str:
    return f"ID: {conv['conversation_id']}\n{_dialog_to_text(conv)}"


def make_batches(conversations: list) -> list:
    batches, current, current_chars = [], [], 0
    for conv in conversations:
        item_len = len(conversation_to_prompt_item(conv))
        if current and (len(current) >= MAX_BATCH_DIALOGS or current_chars + item_len > MAX_BATCH_CHARS):
            batches.append(current)
            current, current_chars = [], 0
        current.append(conv)
        current_chars += item_len
    if current:
        batches.append(current)
    return batches


def build_analysis_prompt(batch: list) -> str:
    dialogs = "\n\n--- ДИАЛОГ ---\n\n".join(conversation_to_prompt_item(c) for c in batch)
    return f"""Найди только очевидные проблемы сотрудника в категориях ГРУБОСТЬ, НЕКОМПЕТЕНТНОСТЬ, КОНФЛИКТ.
Верни JSON:
{{
  "issues": [
    {{
      "conversation_id": "ID",
      "problems": [
        {{
          "category": "ГРУБОСТЬ | НЕКОМПЕТЕНТНОСТЬ | КОНФЛИКТ",
          "employee_quote": "точная цитата сотрудника",
          "client_quote": "точная цитата клиента или пустая строка",
          "reasoning": "коротко, до 160 символов",
          "severity": "высокая | средняя | низкая",
          "confidence": 0.95
        }}
      ]
    }}
  ]
}}

Если проблем нет — {{"issues":[]}}.

ДИАЛОГИ:
{dialogs}"""
