"""
Telegram: форматирование и отправка отчёта.
"""
import time
from collections import Counter
from datetime import datetime

import requests

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEDUP_WINDOW_DAYS, MoscowTZ
from app.logger import logger
from app.utils.text import clean_text, normalize_category


def _send_chunk(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Telegram HTTP {resp.status_code}: {resp.text[:800]}")


def send_telegram(text: str) -> None:
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [text]
    for i, chunk in enumerate(chunks, start=1):
        _send_chunk(chunk)
        logger.info(f"[Telegram] Часть {i}/{len(chunks)} ({len(chunk)} симв.)")
        time.sleep(1)


def _severity_key(severity: str) -> str:
    severity = (severity or "средняя").strip().lower()
    if severity in {"высокая", "средняя", "низкая"}:
        return severity
    return "средняя"


def _severity_emoji(severity: str) -> str:
    return {"высокая": "🔴", "средняя": "🟡", "низкая": "🟢"}.get(_severity_key(severity), "⚪")


def _source_name(chat_type: str) -> str:
    chat_type = clean_text(chat_type)
    if chat_type.lower().startswith("wazzup"):
        return "Wazzup"
    return chat_type or "Неизвестный источник"


def _source_label(chat_type: str) -> str:
    source = _source_name(chat_type)
    lower = source.lower()
    if lower.startswith("wazzup"):
        emoji = "📲"
    elif source == "Диспетчеры":
        emoji = "💬"
    elif source == "Менеджеры подписок":
        emoji = "👥"
    elif source == "Клиентское приложение":
        emoji = "📱"
    else:
        emoji = "▫️"
    return f"{emoji} {source}"


def _topic_name(issue: dict) -> str:
    return clean_text(issue.get("topic")) or "Другое"


def _shorten(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _quote(text: str, limit: int = 220) -> str:
    text = _shorten(text, limit)
    if not text:
        return ""
    return f"«{text}»"


def _risk_text(problem: dict) -> str:
    text = clean_text(problem.get("reasoning")) or clean_text(problem.get("description"))
    for marker in ("Цитата сотрудника:", "Реакция на:"):
        pos = text.find(marker)
        if pos >= 0:
            text = text[:pos]
    return _shorten(text.strip(" .;—-") or "Проблема требует проверки руководителем.", 180)


def _priority(problem: dict) -> str:
    priority = clean_text(problem.get("priority")).upper()
    if priority in {"P1", "P2", "P3"}:
        return priority
    category = normalize_category(problem.get("category"))
    severity = _severity_key(problem.get("severity"))
    if category in {"БЕЗ_ОТВЕТА", "ГРУБОСТЬ"}:
        return "P1"
    if category == "КОНФЛИКТ" and severity == "высокая":
        return "P1"
    if category in {"КОНФЛИКТ", "НЕКОМПЕТЕНТНОСТЬ"} and severity in {"высокая", "средняя"}:
        return "P2"
    return "P3"


def _flatten_issues(issues: list) -> list:
    rows = []
    for issue in issues:
        for problem in issue.get("problems", []):
            rows.append({
                "issue": issue,
                "problem": problem,
                "category": normalize_category(problem.get("category")),
                "severity": _severity_key(problem.get("severity")),
                "priority": _priority(problem),
            })
    return rows


def _analysis_status_text(run_status: str, analysis_stats: dict | None) -> str:
    if run_status == "error":
        return "❌ ошибка"
    if run_status == "partial":
        return "⚠️ частичный"
    return "✅ полный"


def _partial_analysis_warning(run_status: str, analysis_stats: dict | None) -> str:
    if run_status != "partial":
        return ""
    if analysis_stats:
        processed = int(analysis_stats.get("ai_processed") or 0)
        candidates = int(analysis_stats.get("ai_candidates") or analysis_stats.get("sent_to_ai") or 0)
        if candidates:
            return f"⚠️ Анализ выполнен частично: AI обработал {processed} из {candidates} кандидатов."
    return "⚠️ Анализ выполнен частично: AI был недоступен на части диалогов."


def format_report(fresh_issues: list, total_count: int, period: dict,
                  run_status: str, weekly_top: list, analysis_stats: dict | None = None) -> str:
    start_msk = period["report_start_msk"]
    end_msk = period["report_end_msk"]
    period_str = f"{start_msk:%d.%m %H:%M} — {end_msk:%d.%m %H:%M} МСК"

    rows = _flatten_issues(fresh_issues)
    total_new = len(rows)
    hidden_repeats = max(0, total_count - total_new)
    status_text = _analysis_status_text(run_status, analysis_stats)
    partial_warning = _partial_analysis_warning(run_status, analysis_stats)

    if not fresh_issues:
        body = (
            f"🔍 STAX AI Analyzer\n"
            f"Период: {period_str}\n"
            f"\n📊 Сводка\n"
            f"Проблемных диалогов: 0\n"
            f"Всего проблем: 0\n"
            f"Повторов скрыто: {hidden_repeats}\n"
            f"Статус анализа: {status_text}\n"
            f"\nПроблем не обнаружено."
        )
        if partial_warning:
            body = body.replace("\n\nПроблем не обнаружено.", f"\n{partial_warning}\n\nПроблем не обнаружено.")
        if total_count > 0:
            body += f"\n(Найдено {total_count} проблем, все уже репортились ранее за {DEDUP_WINDOW_DAYS} дней.)"
    else:
        sev_counts = Counter(row["severity"] for row in rows)
        category_counts = Counter(row["category"] for row in rows)
        source_counts = Counter(_source_name(row["issue"].get("chat_type")) for row in rows)
        topic_counts = Counter(_topic_name(row["issue"]) for row in rows)

        lines = [
            "🔍 STAX AI Analyzer",
            f"Период: {period_str}",
            "",
            "📊 Сводка",
            f"Проблемных диалогов: {len(fresh_issues)}",
            f"Всего проблем: {total_new}",
            f"Повторов скрыто: {hidden_repeats}",
            f"Статус анализа: {status_text}",
        ]
        if partial_warning:
            lines.append(partial_warning)
        lines += [
            "",
            "🚦 Риски",
            f"🔴 Критичные: {sev_counts['высокая']}",
            f"🟡 Средние: {sev_counts['средняя']}",
            f"🟢 Низкие: {sev_counts['низкая']}",
            "",
            "📌 По категориям",
        ]

        for category, count in category_counts.most_common():
            max_sev = "высокая" if any(r["category"] == category and r["severity"] == "высокая" for r in rows) else (
                "средняя" if any(r["category"] == category and r["severity"] == "средняя" for r in rows) else "низкая"
            )
            lines.append(f"{_severity_emoji(max_sev)} {category} — {count}")

        visible_topics = [(topic, count) for topic, count in topic_counts.most_common(5) if topic != "Другое" or count > 1]
        if visible_topics:
            lines += ["", "🧩 По темам"]
            for topic, count in visible_topics:
                lines.append(f"{topic} — {count}")

        lines += ["", "👥 По источникам"]
        for source, count in source_counts.most_common():
            lines.append(f"{source} — {count}")

        p1_rows = [
            r for r in rows
            if r["severity"] != "низкая" and (r["priority"] == "P1" or r["severity"] == "высокая")
        ]
        p2_rows = [r for r in rows if r not in p1_rows and r["priority"] == "P2" and r["severity"] != "низкая"]
        detail_rows = (p1_rows + p2_rows[:3])[:5]

        lines += ["", "🔥 Проверить в первую очередь"]
        if not detail_rows:
            lines.append("Нет новых P1/P2 диалогов. Низкие проблемы учтены только в статистике.")
        else:
            priority_order = {"P1": 0, "P2": 1, "P3": 2}
            detail_rows = sorted(
                detail_rows,
                key=lambda r: (priority_order.get(r["priority"], 3), {"высокая": 0, "средняя": 1, "низкая": 2}[r["severity"]]),
            )
            for idx, row in enumerate(detail_rows, start=1):
                issue = row["issue"]
                problem = row["problem"]
                emp = clean_text(issue.get("employee")) or "Без ответа"
                chat = _source_label(issue.get("chat_type"))
                conv_id = clean_text(issue.get("conversation_id"))
                link = clean_text(issue.get("dialog_link"))
                first_msg = clean_text(issue.get("first_client_msg"))
                topic = _topic_name(issue)
                client_quote = clean_text(problem.get("client_quote")) or first_msg
                employee_quote = clean_text(problem.get("employee_quote"))
                risk = _risk_text(problem)
                header = f"{idx}. {_severity_emoji(row['severity'])} {row['category']} — {chat}"
                if emp and emp != "Без ответа":
                    header += f" — {emp}"
                lines.append("")
                lines.append(header)
                lines.append(f"⚠️ Риск: {risk}")
                if client_quote:
                    lines.append(f"🙋 Клиент: {_quote(client_quote, 220)}")
                if employee_quote:
                    lines.append(f"👤 Сотрудник: {_quote(employee_quote, 220)}")
                if topic and topic != "Другое":
                    lines.append(f"🏷️ Тема: {topic}")
                if conv_id:
                    lines.append(f"🆔 ID: {conv_id}")
                if link:
                    lines.append(f"🔗 Диалог: {link}")

        if len(detail_rows) < total_new:
            lines.append("")
            lines.append("Показаны только самые важные диалоги. Остальные учтены в статистике.")

        lines += [
            "",
            "🧭 Что сделать",
            "1. Проверить критичные диалоги.",
            "2. Разобрать повторяющиеся проблемы с отделом.",
            "3. Не использовать БЕЗ_ПРИВЕТСТВИЯ как KPI до появления времени сообщений.",
        ]

        body = "\n".join(lines)

    # Еженедельный топ (по воскресеньям)
    if datetime.now(MoscowTZ).weekday() == 6 and weekly_top:
        body += "\n\n📊 Итоги недели — топ сотрудников по проблемам:\n"
        for i, row in enumerate(weekly_top, start=1):
            body += f"{i}. {row['employee']}: {row['problems']} проблем\n"

    body += "\nАнализ выполнен автоматически."
    return body
