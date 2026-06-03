"""
PostgreSQL — подключение и репозиторий.
Заменяет SQLite. Использует psycopg2.
"""
import json

import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta

from app.config import DATABASE_URL, MoscowTZ, DEDUP_WINDOW_DAYS
from app.logger import logger
from app.utils.hashes import hash_description


def get_connection():
    return psycopg2.connect(
        DATABASE_URL,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def init_db(conn) -> None:
    """Создаём таблицы если не существуют."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reported_problems (
                id BIGSERIAL PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                employee TEXT,
                chat_type TEXT,
                category TEXT NOT NULL,
                description_hash TEXT NOT NULL,
                first_reported_at TIMESTAMP NOT NULL,
                last_reported_at TIMESTAMP NOT NULL,
                report_count INTEGER DEFAULT 1
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_reported_problems_dedup
            ON reported_problems(conversation_id, category, description_hash)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS employee_daily_stats (
                id BIGSERIAL PRIMARY KEY,
                date DATE NOT NULL,
                employee TEXT NOT NULL,
                chat_type TEXT,
                total_dialogs INTEGER DEFAULT 0,
                problems_count INTEGER DEFAULT 0,
                UNIQUE(date, employee, chat_type)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_log (
                id BIGSERIAL PRIMARY KEY,
                run_started_at TIMESTAMP NOT NULL,
                run_finished_at TIMESTAMP,
                period_start TIMESTAMP,
                period_end TIMESTAMP,
                total_problems INTEGER,
                ai_batches_total INTEGER,
                ai_batches_failed INTEGER,
                status TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_processed_dialogs (
                id BIGSERIAL PRIMARY KEY,
                analysis_date DATE NOT NULL,
                source TEXT NOT NULL,
                source_scope TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                last_message_key TEXT NOT NULL,
                processed_at TIMESTAMP NOT NULL,
                UNIQUE(analysis_date, source, source_scope, conversation_id, last_message_key)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_processed_dialogs_lookup
            ON ai_processed_dialogs(analysis_date, source, source_scope)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS integration_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_analysis_queue (
                id BIGSERIAL PRIMARY KEY,
                analysis_date DATE NOT NULL,
                source TEXT NOT NULL,
                source_scope TEXT NOT NULL,
                source_name TEXT,
                conversation_id TEXT NOT NULL,
                last_message_key TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'P3',
                reason TEXT,
                payload JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                queued_at TIMESTAMP NOT NULL,
                processed_at TIMESTAMP,
                last_error TEXT,
                UNIQUE(source, source_scope, conversation_id, last_message_key)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_analysis_queue_pick
            ON ai_analysis_queue(status, priority, queued_at)
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_quality_report_items (
                id BIGSERIAL PRIMARY KEY,
                report_date DATE NOT NULL,
                conversation_id TEXT NOT NULL,
                issue JSONB NOT NULL,
                problem_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL,
                reported_at TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_quality_report_items_pending
            ON ai_quality_report_items(report_date, reported_at)
        """)
    conn.commit()
    logger.info("БД инициализирована")


def is_duplicate_problem(conn, conv_id: str, category: str, description: str) -> bool:
    cutoff = datetime.now(MoscowTZ) - timedelta(days=DEDUP_WINDOW_DAYS)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM reported_problems
            WHERE conversation_id = %s
              AND category = %s
              AND description_hash = %s
              AND last_reported_at >= %s
            LIMIT 1
        """, (conv_id, category, hash_description(description), cutoff))
        return cur.fetchone() is not None


def record_problem(conn, conv_id: str, employee: str, chat_type: str,
                   category: str, description: str) -> None:
    now = datetime.now(MoscowTZ)
    desc_hash = hash_description(description)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, report_count FROM reported_problems
            WHERE conversation_id = %s AND category = %s AND description_hash = %s
            ORDER BY id DESC LIMIT 1
        """, (conv_id, category, desc_hash))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE reported_problems SET last_reported_at = %s, report_count = %s WHERE id = %s",
                (now, existing[1] + 1, existing[0])
            )
        else:
            cur.execute("""
                INSERT INTO reported_problems
                    (conversation_id, employee, chat_type, category, description_hash,
                     first_reported_at, last_reported_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (conv_id, employee, chat_type, category, desc_hash, now, now))


def update_employee_stats(conn, employee: str, chat_type: str,
                          total_dialogs: int, problems_count: int,
                          stats_date=None) -> None:
    date_value = stats_date or datetime.now(MoscowTZ).date()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO employee_daily_stats (date, employee, chat_type, total_dialogs, problems_count)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (date, employee, chat_type) DO UPDATE SET
                total_dialogs = employee_daily_stats.total_dialogs + EXCLUDED.total_dialogs,
                problems_count = employee_daily_stats.problems_count + EXCLUDED.problems_count
        """, (date_value, employee, chat_type, total_dialogs, problems_count))


def get_ai_processed_conversation_keys(conn, analysis_date, source: str, source_scope: str) -> set[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT conversation_id, last_message_key
            FROM ai_processed_dialogs
            WHERE analysis_date = %s
              AND source = %s
              AND source_scope = %s
        """, (analysis_date, source, source_scope))
        return {(str(row[0]), str(row[1])) for row in cur.fetchall()}


def record_ai_processed_dialogs(conn, analysis_date, records: list[dict]) -> None:
    if not records:
        return

    now = datetime.now(MoscowTZ)
    rows = [
        (
            analysis_date,
            clean_text_or_empty(row.get("source")),
            clean_text_or_empty(row.get("source_scope")),
            clean_text_or_empty(row.get("conversation_id")),
            clean_text_or_empty(row.get("last_message_key")),
            now,
        )
        for row in records
        if row.get("conversation_id") and row.get("last_message_key")
    ]
    if not rows:
        return

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO ai_processed_dialogs (
                analysis_date, source, source_scope, conversation_id,
                last_message_key, processed_at
            )
            VALUES %s
            ON CONFLICT (analysis_date, source, source_scope, conversation_id, last_message_key)
            DO UPDATE SET processed_at = EXCLUDED.processed_at
        """, rows)


def get_integration_state(conn, key: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM integration_state WHERE key = %s", (key,))
        row = cur.fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def set_integration_state(conn, key: str, value: str) -> None:
    now = datetime.now(MoscowTZ)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration_state (key, value, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at
        """, (key, str(value), now))


def enqueue_ai_analysis_candidates(conn, analysis_date, records: list[dict]) -> int:
    if not records:
        return 0

    now = datetime.now(MoscowTZ)
    rows = []
    for row in records:
        payload = row.get("payload")
        conversation_id = clean_text_or_empty(row.get("conversation_id"))
        last_message_key = clean_text_or_empty(row.get("last_message_key"))
        if not payload or not conversation_id or not last_message_key:
            continue
        rows.append((
            analysis_date,
            clean_text_or_empty(row.get("source")),
            clean_text_or_empty(row.get("source_scope")),
            clean_text_or_empty(row.get("source_name")),
            conversation_id,
            last_message_key,
            clean_text_or_empty(row.get("priority")) or "P3",
            clean_text_or_empty(row.get("reason")),
            psycopg2.extras.Json(
                payload,
                dumps=lambda value: json.dumps(value, ensure_ascii=False, default=str),
            ),
            now,
        ))
    if not rows:
        return 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO ai_analysis_queue (
                analysis_date, source, source_scope, source_name,
                conversation_id, last_message_key, priority, reason,
                payload, queued_at
            )
            VALUES %s
            ON CONFLICT (source, source_scope, conversation_id, last_message_key)
            DO UPDATE SET
                analysis_date = EXCLUDED.analysis_date,
                source_name = EXCLUDED.source_name,
                priority = EXCLUDED.priority,
                reason = EXCLUDED.reason,
                payload = EXCLUDED.payload,
                queued_at = EXCLUDED.queued_at,
                last_error = NULL
            WHERE ai_analysis_queue.status <> 'processed'
        """, rows)
        return cur.rowcount


def fetch_ai_queue_batch(conn, limit: int, include_low_priority: bool = False) -> list[dict]:
    priority_filter = "" if include_low_priority else "AND priority <> 'P3'"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""
            SELECT id, analysis_date, source, source_scope, source_name, conversation_id,
                   last_message_key, priority, reason, payload
            FROM ai_analysis_queue
            WHERE status = 'pending'
              AND attempts < 5
              {priority_filter}
            ORDER BY
                CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                queued_at ASC,
                id ASC
            LIMIT %s
        """, (int(limit),))
        rows = cur.fetchall()

    result = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        item = dict(row)
        item["payload"] = payload
        result.append(item)
    return result


def store_ai_quality_report_items(conn, report_date, issues: list[dict]) -> int:
    if not issues:
        return 0

    now = datetime.now(MoscowTZ)
    rows = []
    for issue in issues:
        conversation_id = clean_text_or_empty(issue.get("conversation_id"))
        problems = issue.get("problems") or []
        if not conversation_id or not problems:
            continue
        rows.append((
            report_date,
            conversation_id,
            psycopg2.extras.Json(
                issue,
                dumps=lambda value: json.dumps(value, ensure_ascii=False, default=str),
            ),
            len(problems),
            now,
        ))
    if not rows:
        return 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO ai_quality_report_items (
                report_date, conversation_id, issue, problem_count, created_at
            )
            VALUES %s
        """, rows)
        return cur.rowcount


def fetch_pending_ai_quality_report_items(conn, report_date) -> tuple[list[int], list[dict]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, issue
            FROM ai_quality_report_items
            WHERE report_date = %s
              AND reported_at IS NULL
            ORDER BY id ASC
        """, (report_date,))
        rows = cur.fetchall()

    ids = []
    issues = []
    for row in rows:
        ids.append(int(row["id"]))
        issue = row.get("issue")
        if isinstance(issue, str):
            issue = json.loads(issue)
        issues.append(dict(issue))
    return ids, issues


def mark_ai_quality_report_items_reported(conn, item_ids: list[int]) -> None:
    if not item_ids:
        return
    now = datetime.now(MoscowTZ)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE ai_quality_report_items
            SET reported_at = %s
            WHERE id = ANY(%s)
        """, (now, list(item_ids)))


def mark_ai_queue_processed(conn, queue_ids: list[int]) -> None:
    if not queue_ids:
        return
    now = datetime.now(MoscowTZ)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE ai_analysis_queue
            SET status = 'processed',
                processed_at = %s,
                last_error = NULL
            WHERE id = ANY(%s)
        """, (now, list(queue_ids)))


def mark_ai_queue_attempted(conn, queue_ids: list[int], error: str = "") -> None:
    if not queue_ids:
        return
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE ai_analysis_queue
            SET attempts = attempts + 1,
                last_error = %s
            WHERE id = ANY(%s)
              AND status = 'pending'
        """, (clean_text_or_empty(error)[:500], list(queue_ids)))


def get_ai_queue_counts(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT priority, COUNT(*)
            FROM ai_analysis_queue
            WHERE status = 'pending'
            GROUP BY priority
        """)
        rows = cur.fetchall()
    counts = {"P1": 0, "P2": 0, "P3": 0, "total": 0}
    for priority, count in rows:
        key = clean_text_or_empty(priority).upper() or "P3"
        value = int(count or 0)
        counts[key] = counts.get(key, 0) + value
        counts["total"] += value
    return counts


def clean_text_or_empty(value) -> str:
    return str(value or "").strip()


def get_weekly_top_offenders(conn, top_n: int = 3) -> list:
    cutoff = (datetime.now(MoscowTZ) - timedelta(days=7)).date()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT employee, SUM(problems_count) AS p, SUM(total_dialogs) AS d
            FROM employee_daily_stats
            WHERE date >= %s
              AND employee IS NOT NULL
              AND employee NOT IN ('Без ответа', '')
            GROUP BY employee
            HAVING SUM(problems_count) > 0
            ORDER BY p DESC
            LIMIT %s
        """, (cutoff, top_n))
        return [{"employee": r[0], "problems": r[1], "dialogs": r[2]} for r in cur.fetchall()]


def record_run_start(conn, period_start, period_end) -> int:
    now = datetime.now(MoscowTZ)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO run_log (run_started_at, period_start, period_end, status)
            VALUES (%s, %s, %s, 'running')
            RETURNING id
        """, (now, period_start, period_end))
        return cur.fetchone()[0]


def record_run_finish(conn, run_id: int, total_problems: int,
                      ai_batches_total: int, ai_batches_failed: int, status: str) -> None:
    now = datetime.now(MoscowTZ)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE run_log
            SET run_finished_at = %s,
                total_problems = %s,
                ai_batches_total = %s,
                ai_batches_failed = %s,
                status = %s
            WHERE id = %s
        """, (now, total_problems, ai_batches_total, ai_batches_failed, status, run_id))
