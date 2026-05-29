"""
Утилиты для работы со временем.
"""
from datetime import datetime, time, timedelta

from app.config import MoscowTZ


def _as_moscow(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(MoscowTZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=MoscowTZ)
    return now.astimezone(MoscowTZ)


def get_period_timestamps(now: datetime | None = None) -> dict:
    """
    Основной запуск в 20:00 МСК проверяет текущий день 00:00-19:00.
    Ночной запуск в 02:00 МСК добирает тот же день уже до полуночи.
    """
    now_msk = _as_moscow(now)
    is_retry_run = now_msk.hour < 6
    target_date = (now_msk - timedelta(days=1)).date() if is_retry_run else now_msk.date()

    report_start_msk = datetime.combine(target_date, time.min, tzinfo=MoscowTZ)
    if is_retry_run:
        report_end_msk = report_start_msk + timedelta(days=1)
        period_mode = "retry"
    else:
        report_end_msk = report_start_msk + timedelta(hours=19)
        period_mode = "main"

    return {
        "fetch_start_ts": int(report_start_msk.timestamp()),
        "fetch_end_ts": int(report_end_msk.timestamp()),
        "report_start_ts": int(report_start_msk.timestamp()),
        "report_end_ts": int(report_end_msk.timestamp()),
        "report_start_msk": report_start_msk,
        "report_end_msk": report_end_msk,
        "analysis_date": target_date,
        "period_mode": period_mode,
    }
