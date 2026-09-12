import unittest

from sqlalchemy import UniqueConstraint

from app.models import PowerMeasurement


class MeasurementUniqueConstraintTests(unittest.TestCase):
    def test_model_declares_channel_timestamp_uniqueness(self):
        constraints = [
            constraint
            for constraint in PowerMeasurement.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        ]

        matching = [
            constraint
            for constraint in constraints
            if constraint.name
            == "uq_power_measurements_channel_measured_at"
        ]

        self.assertEqual(len(matching), 1)
        self.assertEqual(
            [column.name for column in matching[0].columns],
            ["channel_id", "measured_at"],
        )


if __name__ == "__main__":
    unittest.main()
