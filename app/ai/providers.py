"""
AI провайдеры: Gemini и Groq.
Circuit Breaker: 429 (rate limit) → провайдер мёртв до конца прогона.
                 503 (перегрузка) → провайдер временно выключен после 3 ошибок подряд.
"""
import time

import requests

from app.config import (
    GROQ_API_KEY, GEMINI_API_KEY,
    GROQ_MODEL, GEMINI_MODEL,
    AI_TIMEOUT, SERVER_OVERLOAD_STREAK_THRESHOLD,
)
from app.logger import logger

# ── Глобальное состояние circuit breaker ──────────────────────────────────────
_STATE = {
    "calls": 0,
    "failures": 0,
    "gemini_dead": False,
    "groq_dead": False,
    "gemini_503_streak": 0,
    "groq_503_streak": 0,
}


def get_stats() -> dict:
    return {"calls": _STATE["calls"], "failures": _STATE["failures"]}


# ── Низкоуровневые вызовы ─────────────────────────────────────────────────────

def _call_groq(system_msg: str, user_msg: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.05,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=AI_TIMEOUT)
    if resp.status_code == 429:
        raise RuntimeError("GROQ_429_RATE_LIMIT")
    if resp.status_code >= 400:
        raise RuntimeError(f"GROQ_HTTP_{resp.status_code}: {resp.text[:800]}")
    return resp.json()["choices"][0]["message"]["content"]


def _call_gemini(system_msg: str, user_msg: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": f"{system_msg}\n\n{user_msg}"}]}],
        "generationConfig": {"temperature": 0.05, "response_mime_type": "application/json"},
    }
    resp = requests.post(url, json=payload, timeout=AI_TIMEOUT)
    if resp.status_code == 429:
        raise RuntimeError("GEMINI_429_RATE_LIMIT")
    if resp.status_code >= 400:
        raise RuntimeError(f"GEMINI_HTTP_{resp.status_code}: {resp.text[:800]}")
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


# ── Circuit Breaker helpers ───────────────────────────────────────────────────

def _is_overloaded(name: str) -> bool:
    key = f"{name.lower()}_503_streak"
    return _STATE.get(key, 0) >= SERVER_OVERLOAD_STREAK_THRESHOLD


def _mark_dead(name: str) -> None:
    _STATE[f"{name.lower()}_dead"] = True
    logger.warning(f"[AI] {name} помечен как RATE_LIMITED до конца прогона")


def _incr_503(name: str) -> None:
    key = f"{name.lower()}_503_streak"
    _STATE[key] = _STATE.get(key, 0) + 1
    streak = _STATE[key]
    logger.warning(f"[AI] {name} 503-streak: {streak}/{SERVER_OVERLOAD_STREAK_THRESHOLD}")
    if streak >= SERVER_OVERLOAD_STREAK_THRESHOLD:
        logger.warning(f"[AI] {name} временно лёг (перегрузка), переключаюсь на fallback")


def _reset_503(name: str) -> None:
    _STATE[f"{name.lower()}_503_streak"] = 0


# ── Публичный интерфейс ───────────────────────────────────────────────────────

def call_ai(system_msg: str, user_msg: str, retries: int = 2) -> str:
    """
    Приоритет:
    1. Gemini 2.5 Flash (1500 RPD, 1M TPM)
    2. Groq llama-3.3-70b (~30k TPD)

    Если оба умерли — лучше пропустить пачку, чем использовать слабую модель.
    """
    _STATE["calls"] += 1
    last_error = None

    providers = []
    if GEMINI_API_KEY and not _STATE["gemini_dead"] and not _is_overloaded("gemini"):
        providers.append(("Gemini", _call_gemini))
    if GROQ_API_KEY and not _STATE["groq_dead"] and not _is_overloaded("groq"):
        providers.append(("Groq", _call_groq))

    if not providers:
        _STATE["failures"] += 1
        raise RuntimeError("Все AI-провайдеры исчерпаны (rate limit или перегрузка)")

    for name, fn in providers:
        for attempt in range(1, retries + 1):
            try:
                logger.info(f"[AI] {name}, попытка {attempt}/{retries}")
                result = fn(system_msg, user_msg)
                _reset_503(name)
                return result
            except Exception as e:
                last_error = e
                err = str(e)
                logger.warning(f"[AI] {name} ошибка: {err[:200]}")

                if "429" in err or "RATE_LIMIT" in err:
                    if attempt >= 2:
                        _mark_dead(name)
                        break
                    wait = min(20 * attempt, 40)
                    logger.info(f"[AI] Лимит, пауза {wait} сек...")
                    time.sleep(wait)
                elif "503" in err or "UNAVAILABLE" in err or "high demand" in err:
                    if attempt >= 2:
                        _incr_503(name)
                        break
                    wait = 10 * attempt
                    logger.info(f"[AI] Перегрузка модели, пауза {wait} сек...")
                    time.sleep(wait)
                else:
                    time.sleep(3)

    _STATE["failures"] += 1
    raise RuntimeError(f"AI не ответил. Последняя ошибка: {last_error}")
