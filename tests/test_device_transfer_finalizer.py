import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.device_transfer_archive import list_former_owner_archives
from app.device_transfer_finalizer import complete_due_transfer
from app.device_transfer_store import (
    TransferRejected,
    issue_reset_challenge,
    stage_verified_transfer,
)
from app.factory_reset_proof import reset_proof_message
from app.models import (
    Device,
    DeviceChannel,
    DeviceMembership,
    DeviceTransfer,
    DeviceTransferMqttCutover,
    DeviceTransferNotice,
    PowerMeasurement,
    PowerMeasurementAttribution,
    User,
)


NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
UID = "SM-A1B2C3D4E5F6"
KEY = bytes(range(32))


class TransferFinalizerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        old = User(email="old@example.test", password_hash="unused", email_verified=True)
        new = User(email="new@example.test", password_hash="unused", email_verified=True)
        member = User(email="member@example.test", password_hash="unused", email_verified=True)
        device = Device(
            device_uid=UID,
            mqtt_device_id="bench",
            name="Salon",
            created_at=NOW - timedelta(days=1),
        )
        self.db.add_all([old, new, member, device])
        self.db.flush()
        self.old_id, self.new_id, self.member_id = old.id, new.id, member.id
        self.device_id = device.id
        self.channel = DeviceChannel(device_id=device.id, channel_key="power_1", name="Cuisine")
        self.db.add_all([
            self.channel,
            DeviceMembership(device_id=device.id, user_id=old.id, role="owner"),
            DeviceMembership(device_id=device.id, user_id=member.id, role="member"),
        ])
        self.db.flush()
        self.measurement = PowerMeasurement(
            channel_id=self.channel.id, measured_at=NOW - timedelta(hours=1)
        )
        self.db.add(self.measurement)
        self.db.flush()
        self.db.add(PowerMeasurementAttribution(
            measurement_id=self.measurement.id,
            device_id=device.id,
            owner_user_id=old.id,
            epoch_transfer_id=None,
            attributed_at=NOW - timedelta(hours=1),
        ))
        self.db.commit()
        challenge = issue_reset_challenge(self.db, device_uid=UID, now=NOW)
        tag = hmac.new(
            KEY, reset_proof_message(UID, 1, challenge.challenge_hex), hashlib.sha256
        ).hexdigest().upper()
        self.transfer = stage_verified_transfer(
            self.db,
            device_uid=UID,
            next_owner_user_id=self.new_id,
            reset_generation=1,
            challenge_id=challenge.id,
            challenge_hex=challenge.challenge_hex,
            tag_hex=tag,
            factory_key=KEY,
            now=NOW,
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_cutover(self, *, device_uid=UID):
        self.db.add(DeviceTransferMqttCutover(
            transfer_id=self.transfer.id,
            device_uid=device_uid,
            broker_reference="broker-cutover-test-1",
            old_access_revoked_at=NOW + timedelta(seconds=20),
            new_access_verified_at=NOW + timedelta(seconds=30),
        ))
        self.db.commit()

    def memberships(self):
        return list(self.db.scalars(
            select(DeviceMembership).where(DeviceMembership.device_id == self.device_id)
        ).all())

    def test_deadline_and_mqtt_cutover_are_both_required(self):
        with self.assertRaisesRegex(TransferRejected, "credential cutover"):
            complete_due_transfer(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.add_cutover()
        with self.assertRaisesRegex(TransferRejected, "delay not elapsed"):
            complete_due_transfer(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(seconds=59)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")
        self.assertEqual({m.user_id for m in self.memberships()}, {self.old_id, self.member_id})

    def test_completion_removes_old_access_preserves_archive_and_is_idempotent(self):
        self.add_cutover()
        effective = NOW + timedelta(minutes=1)
        completed = complete_due_transfer(
            self.db, transfer_id=self.transfer.id, now=effective
        )
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.effective_at, effective)
        self.assertEqual([(m.user_id, m.role) for m in self.memberships()], [(self.new_id, "owner")])
        self.assertIsNone(self.db.get(Device, self.device_id).name)
        self.assertIsNone(self.db.get(DeviceChannel, self.channel.id).name)
        self.assertIsNotNone(self.db.get(PowerMeasurement, self.measurement.id))
        self.assertEqual(
            self.db.get(PowerMeasurementAttribution, self.measurement.id).owner_user_id,
            self.old_id,
        )
        archives = list_former_owner_archives(self.db, user_id=self.old_id)
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].channel_ids, [self.channel.id])
        self.assertEqual(list_former_owner_archives(self.db, user_id=self.new_id), [])
        notices = list(self.db.scalars(
            select(DeviceTransferNotice).where(DeviceTransferNotice.transfer_id == self.transfer.id)
        ).all())
        self.assertEqual(len(notices), 3)
        self.assertEqual(
            {(n.user_id, n.phase) for n in notices},
            {(self.old_id, "pending"), (self.old_id, "completed"), (self.new_id, "completed")},
        )
        self.assertIn("Salon", next(n.body for n in notices if n.user_id == self.old_id and n.phase == "completed"))
        self.assertNotIn("Salon", next(n.body for n in notices if n.user_id == self.new_id))

        retried = complete_due_transfer(
            self.db, transfer_id=self.transfer.id, now=effective + timedelta(minutes=1)
        )
        self.assertEqual(retried.id, completed.id)
        self.assertEqual(len(list(self.db.scalars(
            select(DeviceTransferNotice).where(DeviceTransferNotice.transfer_id == self.transfer.id)
        ).all())), 3)

    def test_wrong_device_cutover_cannot_complete(self):
        self.add_cutover(device_uid="SM-OTHER")
        with self.assertRaisesRegex(TransferRejected, "credential cutover"):
            complete_due_transfer(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")

    def test_conflicting_notice_rolls_back_every_ownership_change(self):
        self.add_cutover()
        self.db.add(DeviceTransferNotice(
            transfer_id=self.transfer.id,
            user_id=self.old_id,
            phase="completed",
            body="conflict",
            created_at=NOW,
        ))
        self.db.commit()
        with self.assertRaisesRegex(TransferRejected, "conflicting transfer completion"):
            complete_due_transfer(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")
        self.assertEqual({m.user_id for m in self.memberships()}, {self.old_id, self.member_id})


if __name__ == "__main__":
    unittest.main()
