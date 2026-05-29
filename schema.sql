-- STAX AI QA Monitor — схема PostgreSQL
-- Запустить один раз в Neon или psql

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
);

CREATE INDEX IF NOT EXISTS idx_reported_problems_dedup
    ON reported_problems(conversation_id, category, description_hash);

CREATE TABLE IF NOT EXISTS employee_daily_stats (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    employee TEXT NOT NULL,
    chat_type TEXT,
    total_dialogs INTEGER DEFAULT 0,
    problems_count INTEGER DEFAULT 0,
    UNIQUE(date, employee, chat_type)
);

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
);

CREATE TABLE IF NOT EXISTS ai_processed_dialogs (
    id BIGSERIAL PRIMARY KEY,
    analysis_date DATE NOT NULL,
    source TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    last_message_key TEXT NOT NULL,
    processed_at TIMESTAMP NOT NULL,
    UNIQUE(analysis_date, source, source_scope, conversation_id, last_message_key)
);

CREATE INDEX IF NOT EXISTS idx_ai_processed_dialogs_lookup
    ON ai_processed_dialogs(analysis_date, source, source_scope);
