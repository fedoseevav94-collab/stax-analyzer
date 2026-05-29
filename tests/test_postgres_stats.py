from datetime import date
import sys
import types

if "psycopg2" not in sys.modules:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_stub.connect = lambda *args, **kwargs: None
    psycopg2_stub.extras = types.SimpleNamespace(execute_values=lambda *args, **kwargs: None)
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extras"] = psycopg2_stub.extras

from app.db.postgres import update_employee_stats


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


def test_update_employee_stats_uses_passed_stats_date():
    conn = FakeConnection()
    stats_date = date(2026, 5, 27)

    update_employee_stats(conn, "Иванов", "Диспетчеры", 10, 2, stats_date=stats_date)

    _, params = conn.cursor_obj.calls[0]
    assert params[0] == stats_date
    assert params[1:] == ("Иванов", "Диспетчеры", 10, 2)
