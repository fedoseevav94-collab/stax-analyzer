"""
Базовый HTTP-клиент для выгрузки диалогов.
"""
import requests

from app.config import HEADERS, HTTP_TIMEOUT
from app.logger import logger


def fetch_json(url: str, params: dict = None) -> dict:
    response = requests.get(url, headers=HEADERS, params=params or {}, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_conversations(url: str, start_ts: int, end_ts: int, extra_params: dict = None) -> list:
    params = {"start": start_ts, "end": end_ts}
    if extra_params:
        params.update(extra_params)
    try:
        logger.info(f"GET {url} params={params}")
        data = fetch_json(url, params)
        conversations = data.get("conversations", []) or []
        logger.info(f"Ответ: code={data.get('code')}, диалогов={len(conversations)}")
        if data.get("code") == "ok":
            return conversations
        logger.warning(f"Неожиданный ответ: {str(data)[:500]}")
    except requests.exceptions.Timeout:
        logger.error(f"Таймаут: {url}")
    except requests.exceptions.HTTPError as e:
        body = getattr(e.response, "text", "")[:800]
        logger.error(f"HTTP {e.response.status_code}: {body}")
    except Exception as e:
        logger.error(f"{type(e).__name__}: {e}")
    return []
