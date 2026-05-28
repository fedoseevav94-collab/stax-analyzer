"""
STAX AI QA Monitor — точка входа.
Запускается GitHub Actions каждый день в 20:00 МСК.
"""
import sys

from app import config
from app.ai.providers import get_stats as ai_stats
from app.analyzers.pipeline import analyze_source
from app.config import (
    TG_ENDPOINTS, CLIENT_APP_URL, WAZZUP_MESSAGES_URL_TEMPLATE,
    AI_FAILURE_THRESHOLD, CONFIDENCE_THRESHOLD, DEDUP_WINDOW_DAYS, MoscowTZ,
)
from app.db.postgres import (
    get_connection, init_db,
    is_duplicate_problem, record_problem, update_employee_stats,
    get_weekly_top_offenders, record_run_start, record_run_finish,
)
from app.exporters.base import fetch_conversations
from app.exporters.wazzup import fetch_wazzup_channels
from app.logger import logger
from app.telegram.sender import format_report, send_telegram
from app.utils.text import clean_text, normalize_category
from app.utils.time_utils import get_period_timestamps

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


def dedup_and_record(conn, all_issues: list) -> tuple[list, int]:
    """Дедуплицируем через PostgreSQL, записываем каждую проблему."""
    fresh_issues = []
    total_count = 0

    for issue in all_issues:
        cid = issue["conversation_id"]
        emp = clean_text(issue.get("employee")) or "Без ответа"
        ct = issue.get("chat_type", "")
        new_problems = []

        for p in issue["problems"]:
            cat = normalize_category(p.get("category"))
            desc = clean_text(p.get("description"))
            total_count += 1

            if is_duplicate_problem(conn, cid, cat, desc):
                logger.info(f"[DEDUP] {cid} {cat}: повтор")
                record_problem(conn, cid, emp, ct, cat, desc)
                continue

            record_problem(conn, cid, emp, ct, cat, desc)
            new_problems.append(p)

        if new_problems:
            fresh = dict(issue)
            fresh["problems"] = new_problems
            fresh_issues.append(fresh)

    conn.commit()
    return fresh_issues, total_count


def run() -> None:
    config.validate()

    period = get_period_timestamps()
    logger.info("=" * 60)
    logger.info("STAX Analyzer запущен")
    logger.info(f"Период МСК: {period['report_start_msk']} → {period['report_end_msk']}")
    logger.info(f"Confidence threshold: {CONFIDENCE_THRESHOLD}, Dedup window: {DEDUP_WINDOW_DAYS}д")

    conn = get_connection()
    init_db(conn)
    run_id = record_run_start(conn, period["report_start_msk"], period["report_end_msk"])
    conn.commit()

    all_issues: list = []
    employee_totals: dict = {}
    analysis_totals = {
        "loaded": 0,
        "sent_to_ai": 0,
        "skipped_by_filter": 0,
        "ai_candidates": 0,
        "ai_processed": 0,
        "ai_skipped_low_priority": 0,
        "ai_errors": 0,
        "ai_rate_limited": False,
    }

    def merge_analysis_stats(stats: dict) -> None:
        for key in (
            "loaded", "sent_to_ai", "skipped_by_filter", "ai_candidates",
            "ai_processed", "ai_skipped_low_priority", "ai_errors",
        ):
            analysis_totals[key] += int(stats.get(key) or 0)
        analysis_totals["ai_rate_limited"] = (
            analysis_totals["ai_rate_limited"] or bool(stats.get("ai_rate_limited"))
        )

    def add_stats(chat_type: str, dialogs_count: int, issues: list) -> None:
        per_emp: dict = {}
        for issue in issues:
            emp = clean_text(issue.get("employee")) or "Без ответа"
            per_emp[emp] = per_emp.get(emp, 0) + len(issue.get("problems", []))
        for emp, cnt in per_emp.items():
            key = (emp, chat_type)
            employee_totals.setdefault(key, {"total_dialogs": 0, "problems_count": 0})
            employee_totals[key]["problems_count"] += cnt
        key_all = ("_all_", chat_type)
        employee_totals.setdefault(key_all, {"total_dialogs": 0, "problems_count": 0})
        employee_totals[key_all]["total_dialogs"] += dialogs_count

    # ── 1. Telegram ───────────────────────────────────────────────────────────
    for chat_type, cfg in TG_ENDPOINTS.items():
        logger.info("=" * 60)
        logger.info(f"Источник: {chat_type}")
        convs = fetch_conversations(cfg["url"], period["fetch_start_ts"], period["fetch_end_ts"])
        logger.info(f"Диалогов выгружено: {len(convs)}")
        if convs:
            issues, dc, source_stats = analyze_source(convs, chat_type=chat_type, source="telegram",
                                                      chat_id=cfg["chat_id"])
            merge_analysis_stats(source_stats)
            logger.info(f"Проблемных диалогов до дедупа: {len(issues)}")
            all_issues.extend(issues)
            add_stats(chat_type, dc, issues)

    # ── 2. Клиентское приложение ──────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Источник: Клиентское приложение")
    convs = fetch_conversations(CLIENT_APP_URL, period["fetch_start_ts"], period["fetch_end_ts"])
    logger.info(f"Диалогов выгружено: {len(convs)}")
    if convs:
        issues, dc, source_stats = analyze_source(convs, chat_type="Клиентское приложение", source="client_app")
        merge_analysis_stats(source_stats)
        logger.info(f"Проблемных диалогов до дедупа: {len(issues)}")
        all_issues.extend(issues)
        add_stats("Клиентское приложение", dc, issues)

    # ── 3. Wazzup ─────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Источник: Wazzup")
    channels = fetch_wazzup_channels()
    logger.info(f"Найдено каналов: {len(channels)}")
    for ch in channels:
        channel_id = str(ch.get("id", "")).strip()
        channel_title = clean_text(ch.get("title")) or channel_id
        if not channel_id:
            continue
        logger.info(f"Wazzup канал: {channel_title} ({channel_id})")
        wazzup_url = WAZZUP_MESSAGES_URL_TEMPLATE.format(channel_id=channel_id)
        convs = fetch_conversations(
            wazzup_url,
            period["fetch_start_ts"],
            period["fetch_end_ts"],
        )
        logger.info(f"Диалогов выгружено: {len(convs)}")
        if convs:
            issues, dc, source_stats = analyze_source(convs, chat_type=f"Wazzup: {channel_title}",
                                                      source="wazzup", channel_id=channel_id)
            merge_analysis_stats(source_stats)
            logger.info(f"Проблемных диалогов до дедупа: {len(issues)}")
            all_issues.extend(issues)
            add_stats(f"Wazzup: {channel_title}", dc, issues)

    # ── Дедуп и запись ────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Дедупликация через PostgreSQL...")
    # Переподключаемся — соединение могло упасть за время AI-анализа (>10 мин)
    try:
        conn.close()
    except Exception:
        pass
    conn = get_connection()
    fresh_issues, total_count = dedup_and_record(conn, all_issues)
    new_count = sum(len(i["problems"]) for i in fresh_issues)
    logger.info(f"Всего проблем: {total_count}, новых после дедупа: {new_count}")

    for (emp, ct), counts in employee_totals.items():
        update_employee_stats(conn, emp, ct, counts["total_dialogs"], counts["problems_count"])

    stats = ai_stats()
    logger.info(f"AI стат: всего {stats['calls']}, упало {stats['failures']}")
    ai_incomplete = analysis_totals["ai_processed"] < analysis_totals["ai_candidates"]
    if (
        stats["calls"] > 0 and (stats["failures"] / stats["calls"]) > AI_FAILURE_THRESHOLD
    ) or ai_incomplete:
        run_status = "partial"
    else:
        run_status = "ok"

    weekly_top = get_weekly_top_offenders(conn, top_n=3)
    record_run_finish(conn, run_id, total_count, stats["calls"], stats["failures"], run_status)
    conn.commit()
    conn.close()

    # ── Отчёт ─────────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    report = format_report(fresh_issues, total_count, period, run_status, weekly_top, analysis_totals)
    logger.info(report)
    send_telegram(report)
    logger.info("Готово ✓")


if __name__ == "__main__":
    run()
