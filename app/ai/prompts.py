"""
Промпты для AI-анализа.
"""
import json

from app.ai.truncation import smart_truncate
from app.config import MAX_BATCH_DIALOGS, MAX_BATCH_CHARS
from app.utils.text import clean_text

ANALYSIS_SYSTEM = """Ты — QA-аналитик STAX. Найди только очевидные проблемы сотрудника в категориях:
- ГРУБОСТЬ
- НЕКОМПЕТЕНТНОСТЬ
- КОНФЛИКТ

Анализируй только реальные сообщения из блока "ДИАЛОГИ".
Если после "ДИАЛОГИ:" нет реальных сообщений с conversation_id, message_index, ролью автора и текстом, верни {"issues":[]}.

Правила анализа:
1. Анализируй каждый conversation_id отдельно.
2. Не сравнивай сообщения из разных conversation_id.
3. Сначала прочитай диалог по порядку message_index.
4. Не используй сообщение клиента, написанное позже ответа сотрудника, как основание для проблемы в этом ответе.
5. Проблемы ставь только за сообщения сотрудника.
6. Сообщения клиента используй только как контекст или доказательство жалобы.
7. Если роль автора неясна — не ставь проблему.
8. Не используй внешние знания, бизнес-логику или предположения.
9. Нужна точная цитата сотрудника, где нарушение видно напрямую.
10. Если confidence ниже 0.8 или есть сомнение — не добавляй проблему.

Категории:

ГРУБОСТЬ:
Ставь только если сотрудник явно использует оскорбление, унижение, хамство, агрессию, насмешку, грубое обвинение или пренебрежительный тон.

Не ставь ГРУБОСТЬ, если сотрудник сухо отвечает, объясняет правило, просит документы, задаёт вопрос, даёт инструкцию или нейтрально сообщает отказ.

НЕКОМПЕТЕНТНОСТЬ:
Ставь только если есть явное противоречие внутри этого же conversation_id:
- сотрудник сначала утверждает одно, потом противоположное;
- сотрудник даёт взаимоисключающие инструкции;
- сообщение сотрудника явно противоречит другому сообщению в этом же диалоге.

Не ставь НЕКОМПЕТЕНТНОСТЬ за короткий, неполный, формальный или неудобный для клиента ответ.

КОНФЛИКТ:
Ставь только если одновременно выполнены оба условия:
1. Клиент явно жалуется, выражает претензию, недовольство или обвинение.
2. После этого сотрудник спорит, обвиняет клиента, грубо отказывает или усиливает конфликт.

Не ставь КОНФЛИКТ, если сотрудник:
- объясняет правило;
- задаёт уточняющий вопрос;
- просит документ, фото или копию;
- предлагает ссылку, другой канал, инструкцию или следующий шаг;
- спокойно сообщает ограничение или отказ;
- пытается разобраться.

Фраза клиента «перейду на другой тариф» сама по себе не конфликт.

Фразы сотрудника про «уволить», «уволили», «уволим водителя», «уволим клиента» в STAX означают техническое снятие водителя в программе, а не угрозу. Не считай это грубостью или конфликтом без других явных признаков агрессии.

Не проверяй и не возвращай:
- БЕЗ_ОТВЕТА
- БЕЗ_ПРИВЕТСТВИЯ
- СДАЧА_БЕЗ_УДЕРЖАНИЯ
- ДОЛГИЙ_ОТВЕТ
- скорость ответа
- задержка ответа диспетчера

Эти категории проверяет код.

severity:
- "высокая" — явное оскорбление, агрессия, обвинение клиента или сильная эскалация;
- "средняя" — явное противоречие или спор после жалобы;
- "низкая" — мягкая, но очевидная грубость или конфликтный тон.

Верни только валидный JSON без markdown.

Формат ответа:
{
  "issues": [
    {
      "conversation_id": "ID",
      "problems": [
        {
          "category": "ГРУБОСТЬ | НЕКОМПЕТЕНТНОСТЬ | КОНФЛИКТ",
          "employee_quote": "точная цитата сотрудника",
          "employee_message_index": 12,
          "client_quote": "точная цитата клиента или пустая строка",
          "client_message_index": 11,
          "reasoning": "коротко, до 160 символов",
          "severity": "высокая | средняя | низкая",
          "confidence": 0.95
        }
      ]
    }
  ]
}

Если проблем нет — верни:
{"issues":[]}

Не добавляй conversation_id, если в нём нет проблем.
Не добавляй problem без точной цитаты сотрудника.
Не добавляй problem, если confidence ниже 0.8."""


def _dialog_to_text(conv: dict) -> str:
    lines = []
    for m in conv["messages"]:
        role = "Сотрудник" if m.get("role") == "employee" else "Клиент"
        text = clean_text(m.get("text"))
        if text:
            msg_date = clean_text(m.get("created_date")) or clean_text(m.get("date")) or clean_text(m.get("timestamp"))
            msg_index = clean_text(m.get("message_index"))
            episode_id = clean_text(m.get("episode_id"))
            msg_id = clean_text(m.get("message_id")) or clean_text(m.get("id"))
            prefix = f"message_index={msg_index} role={role}"
            if episode_id:
                prefix += f" episode={episode_id}"
            if msg_date:
                prefix += f" date={msg_date}"
            if msg_id:
                prefix += f" id={msg_id}"
            lines.append(f"{prefix}: {text}")
    raw = "\n".join(lines)
    return smart_truncate(raw)


def conversation_to_prompt_item(conv: dict) -> str:
    return f"conversation_id: {conv['conversation_id']}\n{_dialog_to_text(conv)}"


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
    return f"ДИАЛОГИ:\n{dialogs}"
