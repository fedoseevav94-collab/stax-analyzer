"""
PostgreSQL — подключение и репозиторий.
Заменяет SQLite. Использует psycopg2.
"""
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
