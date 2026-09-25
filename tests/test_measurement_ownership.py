import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.devices import _persisted_energy_channel
from app.measurement_ownership import (
    MeasurementOwnerUnavailable,
    attach_measurement_owner,
    require_measurement_in_epoch,
    resolve_owner_epoch,
)
from app.models import (
    Device,
    DeviceChannel,
    DeviceMembership,
    DeviceResetChallenge,
    DeviceTransfer,
    PowerMeasurement,
    PowerMeasurementAttribution,
    User,
)


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class MeasurementOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.a = User(email="a@example.test", password_hash="unused")
        self.b = User(email="b@example.test", password_hash="unused")
        self.device = Device(device_uid="SM-000000000015", mqtt_device_id="bench")
        self.db.add_all([self.a, self.b, self.device])
        self.db.flush()
        self.channel = DeviceChannel(device_id=self.device.id, channel_key="power_1")
        self.db.add_all(
            [
                self.channel,
                DeviceMembership(device_id=self.device.id, user_id=self.a.id, role="owner"),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def transfer(self, state):
        challenge = DeviceResetChallenge(
            id="challenge-1",
            device_id=self.device.id,
            challenge_digest="digest",
            created_at=NOW - timedelta(minutes=2),
            expires_at=NOW + timedelta(minutes=2),
            consumed_at=NOW - timedelta(minutes=1),
        )
        self.db.add(challenge)
        transfer = DeviceTransfer(
            device_id=self.device.id,
            previous_owner_user_id=self.a.id,
            next_owner_user_id=self.b.id,
            challenge_id=challenge.id,
            reset_generation=1,
            state=state,
            cause="physical_factory_reset_reassociation",
            requested_at=NOW - timedelta(minutes=1),
            not_before=NOW,
            effective_at=NOW if state == "completed" else None,
        )
        self.db.add(transfer)
        self.db.commit()
        return transfer

    def measurement(self, measured_at):
        row = PowerMeasurement(channel_id=self.channel.id, measured_at=measured_at)
        self.db.add(row)
        self.db.flush()
        return row

    def test_old_measurement_is_rejected_after_transfer_and_new_one_is_scoped(self):
        first_epoch = resolve_owner_epoch(self.db, channel_id=self.channel.id)
        old_row = self.measurement(NOW - timedelta(minutes=5))
        attach_measurement_owner(self.db, measurement=old_row, epoch=first_epoch, now=NOW)
        self.db.commit()

        transfer = self.transfer("completed")
        self.db.delete(self.db.get(DeviceMembership, (self.a.id, self.device.id)))
        self.db.flush()
        self.db.add(
            DeviceMembership(device_id=self.device.id, user_id=self.b.id, role="owner")
        )
        self.db.commit()

        second_epoch = resolve_owner_epoch(self.db, channel_id=self.channel.id)
        self.assertIsNone(_persisted_energy_channel(
            self.db,
            self.channel,
            owner_user_id=self.b.id,
            epoch_transfer_id=transfer.id,
        ))
        # A delayed record from A cannot be relabelled as B's data.
        with self.assertRaises(MeasurementOwnerUnavailable):
            require_measurement_in_epoch(
                second_epoch, measured_at=NOW - timedelta(minutes=4)
            )
        require_measurement_in_epoch(
            second_epoch, measured_at=NOW + timedelta(seconds=1)
        )
        new_row = self.measurement(NOW + timedelta(seconds=1))
        attach_measurement_owner(self.db, measurement=new_row, epoch=second_epoch, now=NOW)
        self.db.commit()

        attribution = self.db.get(PowerMeasurementAttribution, new_row.id)
        visible = _persisted_energy_channel(
            self.db,
            self.channel,
            owner_user_id=self.b.id,
            epoch_transfer_id=transfer.id,
        )
        self.assertIsNotNone(visible)
        self.assertEqual(visible[1].id, new_row.id)
        self.assertEqual((first_epoch.owner_user_id, first_epoch.epoch_transfer_id), (self.a.id, None))
        self.assertEqual((attribution.owner_user_id, attribution.epoch_transfer_id), (self.b.id, transfer.id))
        self.assertEqual(
            self.db.scalar(select(PowerMeasurementAttribution.owner_user_id).where(
                PowerMeasurementAttribution.measurement_id == old_row.id
            )),
            self.a.id,
        )

    def test_pending_transfer_rejects_attribution(self):
        self.transfer("pending")
        with self.assertRaises(MeasurementOwnerUnavailable):
            resolve_owner_epoch(self.db, channel_id=self.channel.id)

    def test_inconsistent_completed_transfer_rejects_attribution(self):
        self.transfer("completed")
        with self.assertRaises(MeasurementOwnerUnavailable):
            resolve_owner_epoch(self.db, channel_id=self.channel.id)

    def test_missing_owner_rejects_attribution(self):
        self.db.delete(self.db.get(DeviceMembership, (self.a.id, self.device.id)))
        self.db.commit()
        with self.assertRaises(MeasurementOwnerUnavailable):
            resolve_owner_epoch(self.db, channel_id=self.channel.id)


if __name__ == "__main__":
    unittest.main()
