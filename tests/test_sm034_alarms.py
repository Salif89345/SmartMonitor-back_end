import unittest

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.alarm_service import acknowledge_alarm, ingest_alarm_snapshot
from app.database import Base
from app.models import AlarmOccurrence, Device, DeviceEvent, User


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def alarm_payload(*, state: str, transition_count: int) -> dict:
    return {
        "ready": True,
        "self_test_passed": True,
        "enabled_rule_count": 1,
        "active_count": 1 if state in {"active", "pending_clear"} else 0,
        "pending_count": 0,
        "transition_sequence": transition_count,
        "items": [
            {
                "id": "temperature_high",
                "state": state,
                "severity": "critical",
                "value": 42.5,
                "threshold": 40.0,
                "transition_count": transition_count,
            }
        ],
    }


class AlarmLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(
            email="alarm@example.com",
            password_hash="test",
            email_verified=True,
        )
        self.device = Device(
            device_uid="SM-A1B2C3D4E5F6",
            mqtt_device_id="atelier",
            name="SmartMonitor Test",
        )
        self.db.add_all([self.user, self.device])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_activation_repeat_clear_and_acknowledgement_are_idempotent(self):
        changes = ingest_alarm_snapshot(
            self.db,
            mqtt_device_id="atelier",
            alarms=alarm_payload(state="active", transition_count=1),
            boot_count=12,
            occurred_at=NOW,
        )
        self.db.commit()
        self.assertEqual(changes, 1)

        repeated = ingest_alarm_snapshot(
            self.db,
            mqtt_device_id="atelier",
            alarms=alarm_payload(state="active", transition_count=1),
            boot_count=12,
            occurred_at=NOW,
        )
        self.db.commit()
        self.assertEqual(repeated, 0)
        self.assertEqual(
            len(self.db.scalars(select(AlarmOccurrence)).all()),
            1,
        )

        occurrence = self.db.scalar(select(AlarmOccurrence))
        acknowledge_alarm(
            self.db,
            occurrence=occurrence,
            user_id=self.user.id,
        )
        acknowledge_alarm(
            self.db,
            occurrence=occurrence,
            user_id=self.user.id,
        )
        self.db.commit()
        self.assertIsNotNone(occurrence.acknowledged_at)

        cleared = ingest_alarm_snapshot(
            self.db,
            mqtt_device_id="atelier",
            alarms=alarm_payload(state="normal", transition_count=2),
            boot_count=12,
            occurred_at=NOW,
        )
        self.db.commit()
        self.assertEqual(cleared, 1)
        self.assertEqual(occurrence.status, "cleared")
        self.assertEqual(occurrence.clear_transition_count, 2)

        event_types = self.db.scalars(
            select(DeviceEvent.event_type).order_by(DeviceEvent.id)
        ).all()
        self.assertEqual(
            event_types,
            ["alarm_activated", "alarm_acknowledged", "alarm_cleared"],
        )

    def test_disabling_rule_closes_open_occurrence(self):
        ingest_alarm_snapshot(
            self.db,
            mqtt_device_id="atelier",
            alarms=alarm_payload(state="active", transition_count=1),
            boot_count=12,
            occurred_at=NOW,
        )
        self.db.commit()

        changes = ingest_alarm_snapshot(
            self.db,
            mqtt_device_id="atelier",
            alarms={"ready": True, "items": []},
            boot_count=12,
            occurred_at=NOW,
        )
        self.db.commit()

        occurrence = self.db.scalar(select(AlarmOccurrence))
        self.assertEqual(changes, 1)
        self.assertEqual(occurrence.status, "cleared")
        event = self.db.scalars(
            select(DeviceEvent).order_by(DeviceEvent.id.desc())
        ).first()
        self.assertEqual(event.event_type, "alarm_cleared")
        self.assertEqual(event.data["reason"], "rule_disabled_or_missing")

    def test_incomplete_snapshot_cannot_clear_open_occurrence(self):
        ingest_alarm_snapshot(
            self.db,
            mqtt_device_id="atelier",
            alarms=alarm_payload(state="active", transition_count=1),
            boot_count=12,
            occurred_at=NOW,
        )
        self.db.commit()

        changes = ingest_alarm_snapshot(
            self.db,
            mqtt_device_id="atelier",
            alarms={"ready": True, "items": [{"id": "invalid item"}]},
            boot_count=12,
            occurred_at=NOW,
        )
        self.db.commit()

        occurrence = self.db.scalar(select(AlarmOccurrence))
        self.assertEqual(changes, 0)
        self.assertEqual(occurrence.status, "active")


if __name__ == "__main__":
    unittest.main()
