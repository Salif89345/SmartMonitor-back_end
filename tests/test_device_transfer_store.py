import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.device_transfer_store import (
    TransferRejected,
    issue_reset_challenge,
    stage_verified_transfer,
)
from app.factory_reset_proof import reset_proof_message
from app.models import (
    Device,
    DeviceMembership,
    DeviceResetChallenge,
    DeviceTransfer,
    DeviceTransferNotice,
    User,
)


UID = "SM-A1B2C3D4E5F6"
KEY = bytes(range(32))
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class DeviceTransferStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        old_owner = User(
            email="old@example.test", password_hash="unused", email_verified=True
        )
        new_owner = User(
            email="new@example.test", password_hash="unused", email_verified=True
        )
        device = Device(device_uid=UID, mqtt_device_id="old-device", name="Salon")
        self.db.add_all((old_owner, new_owner, device))
        self.db.flush()
        self.old_owner_id = old_owner.id
        self.new_owner_id = new_owner.id
        self.device_id = device.id
        self.db.add(
            DeviceMembership(
                user_id=old_owner.id, device_id=device.id, role="owner"
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _valid_request(self, *, generation=2, now=NOW):
        issued = issue_reset_challenge(self.db, device_uid=UID, now=now)
        tag = hmac.new(
            KEY,
            reset_proof_message(UID, generation, issued.challenge_hex),
            hashlib.sha256,
        ).hexdigest().upper()
        return dict(
            device_uid=UID,
            next_owner_user_id=self.new_owner_id,
            reset_generation=generation,
            challenge_id=issued.id,
            challenge_hex=issued.challenge_hex,
            tag_hex=tag,
            factory_key=KEY,
            now=now,
        )

    def test_valid_proof_stages_notice_but_does_not_change_owner(self):
        args = self._valid_request()
        transfer = stage_verified_transfer(self.db, **args)
        self.assertEqual(transfer.state, "pending")
        self.assertEqual(transfer.previous_owner_user_id, self.old_owner_id)
        self.assertEqual(transfer.next_owner_user_id, self.new_owner_id)
        self.assertEqual(transfer.not_before, NOW + timedelta(minutes=1))
        self.assertIsNone(transfer.effective_at)

        with Session(self.engine) as second_session:
            owner = second_session.scalar(
                select(DeviceMembership).where(
                    DeviceMembership.device_id == self.device_id,
                    DeviceMembership.role == "owner",
                )
            )
            self.assertEqual(owner.user_id, self.old_owner_id)
            notice = second_session.scalar(select(DeviceTransferNotice))
            self.assertEqual(notice.user_id, self.old_owner_id)
            self.assertEqual(notice.phase, "pending")
            self.assertIn("action physique", notice.body)
            self.assertIn("Salon", notice.body)
            self.assertNotIn("new@example.test", notice.body)
            challenge = second_session.get(DeviceResetChallenge, args["challenge_id"])
            self.assertIsNotNone(challenge.consumed_at)

    def test_wrong_proof_does_not_consume_challenge_or_create_notice(self):
        args = self._valid_request()
        args["tag_hex"] = "0" * 64
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **args)
        challenge = self.db.get(DeviceResetChallenge, args["challenge_id"])
        self.assertIsNone(challenge.consumed_at)
        self.assertEqual(self.db.scalar(select(func_count(DeviceTransfer))), 0)
        self.assertEqual(self.db.scalar(select(func_count(DeviceTransferNotice))), 0)

    def test_replayed_challenge_and_generation_are_rejected(self):
        args = self._valid_request()
        transfer = stage_verified_transfer(self.db, **args)
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **args)
        transfer.state = "cancelled"  # a new attempt cannot reuse the old epoch
        self.db.commit()
        second = self._valid_request(generation=2, now=NOW + timedelta(minutes=2))
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **second)

    def test_second_request_is_blocked_while_one_is_pending(self):
        stage_verified_transfer(self.db, **self._valid_request())
        second = self._valid_request(generation=3, now=NOW + timedelta(seconds=10))
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **second)
        self.assertEqual(self.db.scalar(select(func_count(DeviceTransfer))), 1)
        self.assertEqual(self.db.scalar(select(func_count(DeviceTransferNotice))), 1)

    def test_wrong_factory_key_is_rejected(self):
        args = self._valid_request()
        args["factory_key"] = b"X" * 32
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **args)
        self.assertEqual(self.db.scalar(select(func_count(DeviceTransfer))), 0)

    def test_challenge_for_another_device_is_rejected(self):
        other_uid = "SM-112233445566"
        self.db.add(Device(device_uid=other_uid, mqtt_device_id="other-device"))
        self.db.commit()
        issued = issue_reset_challenge(self.db, device_uid=other_uid, now=NOW)
        tag = hmac.new(
            KEY, reset_proof_message(UID, 2, issued.challenge_hex), hashlib.sha256
        ).hexdigest().upper()
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(
                self.db,
                device_uid=UID,
                next_owner_user_id=self.new_owner_id,
                reset_generation=2,
                challenge_id=issued.id,
                challenge_hex=issued.challenge_hex,
                tag_hex=tag,
                factory_key=KEY,
                now=NOW,
            )

    def test_expired_challenge_is_rejected(self):
        args = self._valid_request()
        args["now"] = NOW + timedelta(minutes=3)
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **args)

    def test_same_or_unverified_owner_is_rejected(self):
        args = self._valid_request()
        args["next_owner_user_id"] = self.old_owner_id
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **args)
        args["next_owner_user_id"] = self.new_owner_id
        self.db.get(User, self.new_owner_id).email_verified = False
        self.db.commit()
        with self.assertRaises(TransferRejected):
            stage_verified_transfer(self.db, **args)


def func_count(model):
    from sqlalchemy import func

    return func.count(model.id)


if __name__ == "__main__":
    unittest.main()
