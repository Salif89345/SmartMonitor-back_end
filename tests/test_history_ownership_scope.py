import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.history_service import build_detailed_history


START = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, kind):
        self.kind = kind

    def one(self):
        names = ("power_w", "voltage_v", "current_a", "frequency_hz", "power_factor")
        values = {f"{extreme}_{name}": None for name in names for extreme in ("min", "avg", "max")}
        values["max_energy_kwh"] = None
        return SimpleNamespace(**values)

    def scalar_one_or_none(self):
        return None

    def all(self):
        return []


class _RecordingSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return _Result(len(self.statements))


class DetailedHistoryOwnershipScopeTests(unittest.TestCase):
    def test_every_query_is_restricted_to_owner_and_epoch(self):
        db = _RecordingSession()
        build_detailed_history(
            db,
            channel_id=7,
            period_from=START,
            period_to=START + timedelta(hours=1),
            target_points=100,
            owner_user_id=42,
            epoch_transfer_id=9,
        )
        self.assertEqual(len(db.statements), 4)
        for statement in db.statements:
            sql = str(statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            ))
            self.assertIn("power_measurement_attributions", sql)
            self.assertIn("owner_user_id = 42", sql)
            self.assertIn("epoch_transfer_id = 9", sql)

    def test_first_epoch_requires_null_transfer_id(self):
        db = _RecordingSession()
        build_detailed_history(
            db,
            channel_id=7,
            period_from=START,
            period_to=START + timedelta(hours=1),
            target_points=100,
            owner_user_id=42,
        )
        for statement in db.statements:
            sql = str(statement.compile(dialect=postgresql.dialect()))
            self.assertIn("epoch_transfer_id IS NULL", sql)


if __name__ == "__main__":
    unittest.main()
