import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.device_transfer_archive import list_former_owner_archives
from app.device_transfer_finalizer import complete_due_transfer
from app.device_transfer_broker_worker import (
    complete_due_transfer_from_broker,
    due_transfer_ids,
)
from app.device_transfer_store import (
    TransferRejected,
    issue_reset_challenge,
    stage_verified_transfer,
)
from app.factory_reset_proof import reset_proof_message
from app.models import (
    Device,
    DeviceAlarmConfiguration,
    DeviceChannel,
    DeviceMembership,
    DeviceTransfer,
    DeviceTransferMqttCutover,
    DeviceTransferMqttIdentityCheck,
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
        self.identity_verifier = Mock()
        self.identity_verifier.verify.return_value = True
        self.stage_flag = patch(
            "app.device_transfer_store.SM015_TRANSFER_STAGING_ENABLED", True
        )
        self.stage_flag.start()
        self.addCleanup(self.stage_flag.stop)
        self.transfer_flag = patch(
            "app.device_transfer_finalizer.SM015_TRANSFER_STAGING_ENABLED", True
        )
        self.transfer_flag.start()
        self.addCleanup(self.transfer_flag.stop)
        self.worker_flag = patch(
            "app.device_transfer_broker_worker.SM015_TRANSFER_STAGING_ENABLED", True
        )
        self.worker_flag.start()
        self.addCleanup(self.worker_flag.stop)
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

    def add_identity_check(self, *, device_uid=UID, mqtt_device_id="bench", verified_at=None):
        self.db.add(DeviceTransferMqttIdentityCheck(
            transfer_id=self.transfer.id,
            device_uid=device_uid,
            mqtt_device_id=mqtt_device_id,
            broker_reference="broker-identity-test-1",
            verified_at=verified_at or NOW + timedelta(seconds=30),
        ))
        self.db.commit()

    def memberships(self):
        return list(self.db.scalars(
            select(DeviceMembership).where(DeviceMembership.device_id == self.device_id)
        ).all())

    def complete(self, db, **kwargs):
        return complete_due_transfer(
            db, identity_verifier=self.identity_verifier, **kwargs
        )

    def test_deadline_and_mqtt_identity_are_both_required(self):
        with self.assertRaisesRegex(TransferRejected, "device-scoped MQTT identity"):
            self.complete(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.add_identity_check()
        with self.assertRaisesRegex(TransferRejected, "delay not elapsed"):
            self.complete(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(seconds=59)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")
        self.assertEqual({m.user_id for m in self.memberships()}, {self.old_id, self.member_id})

    def test_transfer_remains_disabled_by_default(self):
        self.add_identity_check()
        with patch("app.device_transfer_finalizer.SM015_TRANSFER_STAGING_ENABLED", False):
            with self.assertRaisesRegex(TransferRejected, "ownership transfer is disabled"):
                self.complete(
                    self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
                )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")

    def test_stored_receipt_alone_never_completes_transfer(self):
        self.add_identity_check()
        with self.assertRaisesRegex(TransferRejected, "live broker identity verification"):
            complete_due_transfer(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")

    def test_rejected_or_invalid_broker_answer_cannot_change_owner(self):
        self.add_identity_check()
        for answer in (False, None, "yes"):
            self.identity_verifier.verify.return_value = answer
            with self.assertRaisesRegex(TransferRejected, "live broker identity verification"):
                self.complete(
                    self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
                )
            self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")
            self.assertEqual({m.user_id for m in self.memberships()}, {self.old_id, self.member_id})

    def test_broker_failure_rolls_back_without_changing_owner(self):
        self.add_identity_check()
        self.identity_verifier.verify.side_effect = ConnectionError("broker unavailable")
        with self.assertRaisesRegex(ConnectionError, "broker unavailable"):
            self.complete(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")
        self.assertEqual({m.user_id for m in self.memberships()}, {self.old_id, self.member_id})

    def test_completion_removes_old_access_preserves_archive_and_is_idempotent(self):
        self.db.add(DeviceAlarmConfiguration(
            device_id=self.device_id,
            owner_user_id=self.old_id,
            rules=[{"id": "previous_owner_rule"}],
        ))
        self.db.commit()
        self.add_identity_check()
        effective = NOW + timedelta(minutes=1)
        completed = self.complete(
            self.db, transfer_id=self.transfer.id, now=effective
        )
        self.identity_verifier.verify.assert_called_once_with(
            device_uid=UID,
            mqtt_device_id="bench",
            broker_reference="broker-identity-test-1",
        )
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.effective_at, effective)
        self.assertEqual([(m.user_id, m.role) for m in self.memberships()], [(self.new_id, "owner")])
        self.assertIsNone(self.db.get(Device, self.device_id).name)
        self.assertIsNone(self.db.get(DeviceChannel, self.channel.id).name)
        self.assertIsNone(self.db.get(DeviceAlarmConfiguration, self.device_id))
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

        retried = self.complete(
            self.db, transfer_id=self.transfer.id, now=effective + timedelta(minutes=1)
        )
        self.assertEqual(retried.id, completed.id)
        self.assertEqual(len(list(self.db.scalars(
            select(DeviceTransferNotice).where(DeviceTransferNotice.transfer_id == self.transfer.id)
        ).all())), 3)

    def test_wrong_device_identity_cannot_complete(self):
        self.add_identity_check(device_uid="SM-OTHER")
        with self.assertRaisesRegex(TransferRejected, "device-scoped MQTT identity"):
            self.complete(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")

    def test_wrong_mqtt_identity_cannot_complete(self):
        self.add_identity_check(mqtt_device_id="other-device")
        with self.assertRaisesRegex(TransferRejected, "device-scoped MQTT identity"):
            self.complete(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )

    def test_stale_or_future_identity_check_cannot_complete(self):
        for checked_at, finalization_at in (
            (NOW - timedelta(seconds=1), NOW + timedelta(minutes=1)),
            (NOW + timedelta(minutes=2), NOW + timedelta(minutes=1)),
            (NOW + timedelta(seconds=30), NOW + timedelta(minutes=3)),
        ):
            self.add_identity_check(verified_at=checked_at)
            with self.assertRaisesRegex(TransferRejected, "device-scoped MQTT identity"):
                self.complete(
                    self.db, transfer_id=self.transfer.id, now=finalization_at
                )
            self.db.delete(self.db.get(DeviceTransferMqttIdentityCheck, self.transfer.id))
            self.db.commit()

    def test_legacy_rotation_receipt_cannot_complete_retained_identity_transfer(self):
        self.db.add(DeviceTransferMqttCutover(
            transfer_id=self.transfer.id,
            device_uid=UID,
            broker_reference="obsolete-cutover-test",
            old_access_revoked_at=NOW + timedelta(seconds=20),
            new_access_verified_at=NOW + timedelta(seconds=30),
        ))
        self.db.commit()
        with self.assertRaisesRegex(TransferRejected, "device-scoped MQTT identity"):
            self.complete(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )

    def test_conflicting_notice_rolls_back_every_ownership_change(self):
        self.add_identity_check()
        self.db.add(DeviceTransferNotice(
            transfer_id=self.transfer.id,
            user_id=self.old_id,
            phase="completed",
            body="conflict",
            created_at=NOW,
        ))
        self.db.commit()
        with self.assertRaisesRegex(TransferRejected, "conflicting transfer completion"):
            self.complete(
                self.db, transfer_id=self.transfer.id, now=NOW + timedelta(minutes=1)
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")
        self.assertEqual({m.user_id for m in self.memberships()}, {self.old_id, self.member_id})

    def test_broker_worker_completes_once_with_fresh_live_attestation(self):
        self.identity_verifier.attest.return_value = "broker-control-audit-1"
        effective = NOW + timedelta(minutes=1)
        self.assertEqual(due_transfer_ids(self.db, now=effective), [self.transfer.id])
        result = complete_due_transfer_from_broker(
            self.db, transfer_id=self.transfer.id,
            attestor=self.identity_verifier, now=effective,
        )
        self.assertEqual(result.state, "completed")
        self.identity_verifier.attest.assert_called_once_with(
            device_uid=UID, mqtt_device_id="bench"
        )
        self.identity_verifier.verify.assert_called_once_with(
            device_uid=UID, mqtt_device_id="bench",
            broker_reference="broker-control-audit-1",
        )
        self.assertEqual([(m.user_id, m.role) for m in self.memberships()], [(self.new_id, "owner")])
        self.assertEqual(due_transfer_ids(self.db, now=effective), [])
        complete_due_transfer_from_broker(
            self.db, transfer_id=self.transfer.id,
            attestor=self.identity_verifier, now=effective + timedelta(minutes=1),
        )
        self.identity_verifier.attest.assert_called_once()
        notices = list(self.db.scalars(
            select(DeviceTransferNotice).where(DeviceTransferNotice.transfer_id == self.transfer.id)
        ))
        self.assertEqual(len(notices), 3)

    def test_broker_worker_never_attests_before_delay_or_without_adapter(self):
        self.identity_verifier.attest.return_value = "broker-control-audit-2"
        with self.assertRaisesRegex(TransferRejected, "delay not elapsed"):
            complete_due_transfer_from_broker(
                self.db, transfer_id=self.transfer.id,
                attestor=self.identity_verifier, now=NOW + timedelta(seconds=59),
            )
        self.identity_verifier.attest.assert_not_called()
        self.assertEqual(due_transfer_ids(self.db, now=NOW + timedelta(seconds=59)), [])
        with self.assertRaisesRegex(TransferRejected, "trusted broker attestor required"):
            complete_due_transfer_from_broker(
                self.db, transfer_id=self.transfer.id,
                attestor=None, now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")

    def test_broker_worker_rejection_rolls_back_receipt_and_access(self):
        effective = NOW + timedelta(minutes=1)
        for reference, answer in ((None, True), ("client-says-ok", False), ("bad\nref", True)):
            self.identity_verifier.attest.return_value = reference
            self.identity_verifier.verify.return_value = answer
            with self.assertRaises(TransferRejected):
                complete_due_transfer_from_broker(
                    self.db, transfer_id=self.transfer.id,
                    attestor=self.identity_verifier, now=effective,
                )
            self.assertIsNone(self.db.get(DeviceTransferMqttIdentityCheck, self.transfer.id))
            self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")
            self.assertEqual({m.user_id for m in self.memberships()}, {self.old_id, self.member_id})

    def test_broker_worker_control_plane_outage_keeps_transfer_pending(self):
        self.identity_verifier.attest.side_effect = ConnectionError("control plane unavailable")
        with self.assertRaisesRegex(ConnectionError, "control plane unavailable"):
            complete_due_transfer_from_broker(
                self.db, transfer_id=self.transfer.id,
                attestor=self.identity_verifier, now=NOW + timedelta(minutes=1),
            )
        self.assertIsNone(self.db.get(DeviceTransferMqttIdentityCheck, self.transfer.id))
        self.assertEqual(self.db.get(DeviceTransfer, self.transfer.id).state, "pending")


if __name__ == "__main__":
    unittest.main()
