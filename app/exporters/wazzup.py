"""
Экспортёр Wazzup — выгружает каналы и диалоги.
"""
from app.config import WAZZUP_CHANNELS_URL
from app.exporters.base import fetch_json
from app.logger import logger


def fetch_wazzup_channels() -> list:
    try:
        logger.info(f"GET {WAZZUP_CHANNELS_URL}")
        data = fetch_json(WAZZUP_CHANNELS_URL)
        channels = data.get("channels", []) or []
        logger.info(f"Ответ: code={data.get('code')}, каналов={len(channels)}")
        if data.get("code") == "ok":
            return channels
    except Exception as e:
        logger.error(f"{type(e).__name__}: {e}")
    return []
