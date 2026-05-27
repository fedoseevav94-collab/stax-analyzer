"""
Telegram: форматирование и отправка отчёта.
"""
import time
from datetime import datetime

import requests

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MAX_PROBLEMS_IN_REPORT, DEDUP_WINDOW_DAYS, MoscowTZ
from app.logger import logger


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


def format_report(fresh_issues: list, total_count: int, period: dict,
                  run_status: str, weekly_top: list) -> str:
    start_msk = period["report_start_msk"]
    end_msk = period["report_end_msk"]
    period_str = f"{start_msk:%d.%m.%Y %H:%M} — {end_msk:%d.%m.%Y %H:%M} МСК"

    status_note = ""
    if run_status == "partial":
        status_note = "\n⚠️ Анализ выполнен частично: AI был недоступен на части диалогов.\n"
    elif run_status == "error":
        status_note = "\n❌ Анализ выполнен с ошибками.\n"

    if not fresh_issues:
        body = (
            f"✅ Отчёт STAX AI Analyzer\n"
            f"Период: {period_str}\n"
            f"{status_note}"
            f"Проблем не обнаружено."
        )
        if total_count > 0:
            body += f"\n(Найдено {total_count} проблем, все уже репортились ранее за {DEDUP_WINDOW_DAYS} дней.)"
    else:
        total_new = sum(len(i.get("problems", [])) for i in fresh_issues)

        # Сводка по severity
        sev_counts = {"высокая": 0, "средняя": 0, "низкая": 0}
        for issue in fresh_issues:
            for p in issue.get("problems", []):
                sev = (p.get("severity") or "средняя").lower()
                sev_counts[sev] = sev_counts.get(sev, 0) + 1

        lines = [
            "🔍 Отчёт STAX AI Analyzer",
            f"Период: {period_str}",
        ]
        if status_note:
            lines.append(status_note.strip())

        lines += [
            f"🔴 Критичных: {sev_counts['высокая']}",
            f"🟡 Средних: {sev_counts['средняя']}",
            f"🟢 Низких: {sev_counts['низкая']}",
            f"Диалогов с проблемами: {len(fresh_issues)}",
        ]
        if total_count > total_new:
            lines.append(f"(Ещё {total_count - total_new} повторных — отфильтрованы.)")
        lines.append("")
        lines.append("━" * 20)

        severity_emoji = {"высокая": "🔴", "средняя": "🟡", "низкая": "🟢"}
        n = 1
        for issue in fresh_issues:
            if n > MAX_PROBLEMS_IN_REPORT:
                lines.append(f"…показаны первые {MAX_PROBLEMS_IN_REPORT} проблем.")
                break
            emp = str(issue.get("employee") or "Без ответа").strip()
            chat = str(issue.get("chat_type") or "").strip()
            conv_id = str(issue.get("conversation_id") or "").strip()
            first_msg = str(issue.get("first_client_msg") or "").strip()
            link = str(issue.get("dialog_link") or "").strip()

            lines.append(f"\n👤 {emp} — {chat}")
            for p in issue.get("problems", []):
                sev = (p.get("severity") or "средняя").lower()
                emoji = severity_emoji.get(sev, "⚪")
                cat = str(p.get("category") or "").strip()
                desc = str(p.get("description") or "").strip()
                conf = p.get("confidence")
                lines.append(f"  {emoji} {cat}")
                lines.append(f"  {desc}")
                if conf is not None and float(conf) < 1.0:
                    lines.append(f"  Уверенность AI: {float(conf):.0%}")
                if first_msg:
                    lines.append(f"  Клиент: \"{first_msg}\"")
                if conv_id:
                    lines.append(f"  ID: {conv_id}")
                if link:
                    lines.append(f"  Диалог: {link}")
                n += 1
                if n > MAX_PROBLEMS_IN_REPORT:
                    break
            lines.append("━" * 20)

        body = "\n".join(lines)

    # Еженедельный топ (по воскресеньям)
    if datetime.now(MoscowTZ).weekday() == 6 and weekly_top:
        body += "\n\n📊 Итоги недели — топ сотрудников по проблемам:\n"
        for i, row in enumerate(weekly_top, start=1):
            body += f"{i}. {row['employee']}: {row['problems']} проблем\n"

    body += "\nАнализ выполнен автоматически."
    return body
