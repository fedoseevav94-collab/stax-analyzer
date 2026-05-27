"""
Конфигурация — все переменные окружения в одном месте.
"""
import os
from zoneinfo import ZoneInfo

# ── Обязательные ──────────────────────────────────────────────────────────────
STAX_TOKEN = os.environ.get("STAX_TOKEN", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# ── AI провайдеры ─────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

# ── База данных ───────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# ── Ссылки на фронтенды ───────────────────────────────────────────────────────
WAZZUP_FRONTEND_BASE_URL = os.environ.get("WAZZUP_FRONTEND_BASE_URL", "").strip().rstrip("/")
CLIENT_APP_FRONTEND_BASE_URL = os.environ.get("CLIENT_APP_FRONTEND_BASE_URL", "").strip().rstrip("/")

# ── Параметры анализа ─────────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.9"))
DEDUP_WINDOW_DAYS = int(os.environ.get("DEDUP_WINDOW_DAYS", "14"))
AI_BATCH_DELAY_SECONDS = int(os.environ.get("AI_BATCH_DELAY_SECONDS", "20"))

# ── API endpoints ─────────────────────────────────────────────────────────────
TG_ENDPOINTS = {
    "Диспетчеры": {
        "url": "https://rest-api.stax.ru/v1/test/telegram/chat/export/15/",
        "chat_id": "15",
    },
    "Менеджеры подписок": {
        "url": "https://rest-api.stax.ru/v1/test/telegram/chat/export/14/",
        "chat_id": "14",
    },
}
CLIENT_APP_URL = "https://rest-api.stax.ru/v1/test/client_app/chat/export/"
WAZZUP_CHANNELS_URL = "https://rest-api.stax.ru/v1/test/wazzup/channels/"
WAZZUP_MESSAGES_URL = "https://rest-api.stax.ru/v1/test/wazzup/channels/"

# ── Константы ─────────────────────────────────────────────────────────────────
HTTP_TIMEOUT = 60
AI_TIMEOUT = 120
MAX_DIALOG_CHARS = 6000
MAX_BATCH_DIALOGS = 3
MAX_BATCH_CHARS = 18000
MAX_PROBLEMS_IN_REPORT = 60
CONTEXT_WINDOW_HOURS = 36
REPORT_WINDOW_HOURS = 24
AI_FAILURE_THRESHOLD = 0.5
SERVER_OVERLOAD_STREAK_THRESHOLD = 3

MoscowTZ = ZoneInfo("Europe/Moscow")
HEADERS = {"token": STAX_TOKEN}


def validate():
    """Проверяем обязательные секреты при старте."""
    missing = [n for n, v in {
        "STAX_TOKEN": STAX_TOKEN,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }.items() if not v]
    if missing:
        raise EnvironmentError("Отсутствуют GitHub Secrets: " + ", ".join(missing))
    if not GROQ_API_KEY and not GEMINI_API_KEY:
        raise EnvironmentError("Нужен хотя бы один AI ключ: GROQ_API_KEY или GEMINI_API_KEY")
    if not DATABASE_URL:
        raise EnvironmentError("Отсутствует DATABASE_URL (Neon PostgreSQL)")
