"""
Утилиты для работы со временем.
"""
from datetime import datetime, timedelta

from app.config import MoscowTZ, CONTEXT_WINDOW_HOURS, REPORT_WINDOW_HOURS


def get_period_timestamps() -> dict:
    now_msk = datetime.now(MoscowTZ)
    end_msk = now_msk.replace(hour=20, minute=0, second=0, microsecond=0)
    if now_msk < end_msk:
        end_msk -= timedelta(days=1)
    report_start_msk = end_msk - timedelta(hours=REPORT_WINDOW_HOURS)
    fetch_start_msk = end_msk - timedelta(hours=CONTEXT_WINDOW_HOURS)
    return {
        "fetch_start_ts": int(fetch_start_msk.timestamp()),
        "fetch_end_ts": int(end_msk.timestamp()),
        "report_start_ts": int(report_start_msk.timestamp()),
        "report_end_ts": int(end_msk.timestamp()),
        "report_start_msk": report_start_msk,
        "report_end_msk": end_msk,
    }
