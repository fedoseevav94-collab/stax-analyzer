from datetime import date, datetime

from app.config import MoscowTZ
from app.utils.time_utils import get_period_timestamps


def test_main_run_period():
    period = get_period_timestamps(datetime(2026, 5, 27, 20, 0, tzinfo=MoscowTZ))

    assert period["period_mode"] == "main"
    assert period["analysis_date"] == date(2026, 5, 27)
    assert period["report_start_msk"] == datetime(2026, 5, 27, 0, 0, tzinfo=MoscowTZ)
    assert period["report_end_msk"] == datetime(2026, 5, 27, 19, 0, tzinfo=MoscowTZ)


def test_night_retry_period():
    period = get_period_timestamps(datetime(2026, 5, 28, 2, 0, tzinfo=MoscowTZ))

    assert period["period_mode"] == "retry"
    assert period["analysis_date"] == date(2026, 5, 27)
    assert period["report_start_msk"] == datetime(2026, 5, 27, 0, 0, tzinfo=MoscowTZ)
    assert period["report_end_msk"] == datetime(2026, 5, 28, 0, 0, tzinfo=MoscowTZ)


def test_naive_datetime_is_treated_as_moscow_time():
    period = get_period_timestamps(datetime(2026, 5, 27, 20, 0))

    assert period["period_mode"] == "main"
    assert period["analysis_date"] == date(2026, 5, 27)
    assert period["report_end_msk"] == datetime(2026, 5, 27, 19, 0, tzinfo=MoscowTZ)


def test_manual_main_run_before_planned_end_does_not_request_future_data():
    period = get_period_timestamps(datetime(2026, 5, 27, 12, 30, tzinfo=MoscowTZ))

    assert period["period_mode"] == "main"
    assert period["report_end_msk"] == datetime(2026, 5, 27, 12, 30, tzinfo=MoscowTZ)
