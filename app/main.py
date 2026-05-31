"""
STAX AI QA Monitor — точка входа.
Запускается GitHub Actions:
- 20:00 МСК: основной анализ текущего дня 00:00-19:00.
- 02:00 МСК: ночной добор того же дня до полуночи.
"""
import os
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
    get_ai_processed_conversation_keys, record_ai_processed_dialogs,
)
from app.exporters.base import fetch_conversations
from app.exporters.wazzup import fetch_wazzup_channels
from app.logger import logger
from app.telegram.sender import format_additional_report, format_report, send_telegram_messages
from app.utils.text import clean_text, normalize_category
from app.utils.time_utils import get_period_timestamps

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on", "да"}


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUE_ENV_VALUES


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

    period = get_period_timestamps(schedule_cron=os.getenv("GITHUB_EVENT_SCHEDULE"))
    full_ai_scan = _env_flag("AI_FULL_SCAN")
    ignore_ai_cache = _env_flag("AI_IGNORE_PROCESSED_CACHE") or full_ai_scan
    logger.info("=" * 60)
    logger.info("STAX Analyzer запущен")
    logger.info(f"Период МСК: {period['report_start_msk']} → {period['report_end_msk']}")
    logger.info(f"Режим периода: {period.get('period_mode')} / дата анализа: {period.get('analysis_date')}")
    if full_ai_scan:
        logger.info("AI режим: полный ручной прогон, предфильтр отключён")
    if ignore_ai_cache:
        logger.info("AI cache: отключён для этого запуска")
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
        "ai_skipped_already_processed": 0,
        "return_requests_checked": 0,
        "return_without_retention_found": 0,
        "full_ai_scan": full_ai_scan,
        "ignore_ai_cache": ignore_ai_cache,
        "source_breakdown": [],
    }
    ai_processed_records: list[dict] = []

    def merge_analysis_stats(stats: dict) -> None:
        for key in (
            "loaded", "sent_to_ai", "skipped_by_filter", "ai_candidates",
            "ai_processed", "ai_skipped_low_priority", "ai_errors",
            "ai_skipped_already_processed", "return_requests_checked",
            "return_without_retention_found",
        ):
            analysis_totals[key] += int(stats.get(key) or 0)
        analysis_totals["ai_rate_limited"] = (
            analysis_totals["ai_rate_limited"] or bool(stats.get("ai_rate_limited"))
        )
        if stats.get("source_name"):
            analysis_totals["source_breakdown"].append({
                "source_name": stats.get("source_name"),
                "loaded": int(stats.get("loaded") or 0),
                "sent_to_ai": int(stats.get("sent_to_ai") or 0),
                "ai_processed": int(stats.get("ai_processed") or 0),
                "skipped_by_filter": int(stats.get("skipped_by_filter") or 0),
                "ai_skipped_already_processed": int(stats.get("ai_skipped_already_processed") or 0),
                "return_requests_checked": int(stats.get("return_requests_checked") or 0),
                "return_without_retention_found": int(stats.get("return_without_retention_found") or 0),
            })

    def processed_keys_for(source: str, source_scope: str) -> set[tuple[str, str]]:
        nonlocal conn
        if ignore_ai_cache:
            return set()
        try:
            return get_ai_processed_conversation_keys(conn, period["analysis_date"], source, source_scope)
        except Exception as exc:
            logger.warning(f"[DB] не удалось прочитать AI processed cache, переподключаюсь: {exc}")
            try:
                conn.close()
            except Exception:
                pass
            conn = get_connection()
            return get_ai_processed_conversation_keys(conn, period["analysis_date"], source, source_scope)

    def collect_processed_records(source: str, source_scope: str, stats: dict) -> None:
        for item in stats.get("ai_processed_keys", []) or []:
            ai_processed_records.append({
                "source": source,
                "source_scope": source_scope,
                "conversation_id": item.get("conversation_id"),
                "last_message_key": item.get("last_message_key"),
            })

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
            source_scope = str(cfg["chat_id"])
            skip_keys = processed_keys_for("telegram", source_scope)
            issues, dc, source_stats = analyze_source(convs, chat_type=chat_type, source="telegram",
                                                      chat_id=cfg["chat_id"],
                                                      skip_ai_conversation_keys=skip_keys,
                                                      force_ai_scan=full_ai_scan)
            merge_analysis_stats(source_stats)
            collect_processed_records("telegram", source_scope, source_stats)
            logger.info(f"Проблемных диалогов до дедупа: {len(issues)}")
            all_issues.extend(issues)
            add_stats(chat_type, dc, issues)

    # ── 2. Клиентское приложение ──────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Источник: Клиентское приложение")
    convs = fetch_conversations(CLIENT_APP_URL, period["fetch_start_ts"], period["fetch_end_ts"])
    logger.info(f"Диалогов выгружено: {len(convs)}")
    if convs:
        source_scope = "client_app"
        skip_keys = processed_keys_for("client_app", source_scope)
        issues, dc, source_stats = analyze_source(
            convs,
            chat_type="Клиентское приложение",
            source="client_app",
            skip_ai_conversation_keys=skip_keys,
            force_ai_scan=full_ai_scan,
        )
        merge_analysis_stats(source_stats)
        collect_processed_records("client_app", source_scope, source_stats)
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
            skip_keys = processed_keys_for("wazzup", channel_id)
            issues, dc, source_stats = analyze_source(convs, chat_type=f"Wazzup: {channel_title}",
                                                      source="wazzup", channel_id=channel_id,
                                                      skip_ai_conversation_keys=skip_keys,
                                                      force_ai_scan=full_ai_scan)
            merge_analysis_stats(source_stats)
            collect_processed_records("wazzup", channel_id, source_stats)
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
    record_ai_processed_dialogs(conn, period["analysis_date"], ai_processed_records)
    fresh_issues, total_count = dedup_and_record(conn, all_issues)
    new_count = sum(len(i["problems"]) for i in fresh_issues)
    logger.info(f"Всего проблем: {total_count}, новых после дедупа: {new_count}")

    for (emp, ct), counts in employee_totals.items():
        update_employee_stats(
            conn,
            emp,
            ct,
            counts["total_dialogs"],
            counts["problems_count"],
            stats_date=period["analysis_date"],
        )

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
    additional_report = format_additional_report(fresh_issues)
    if additional_report:
        logger.info(additional_report)
    send_telegram_messages([report, additional_report])
    logger.info("Готово ✓")


if __name__ == "__main__":
    run()
