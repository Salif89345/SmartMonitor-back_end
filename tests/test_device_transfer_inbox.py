import hashlib
import hmac
import unittest
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.device_transfer_inbox import (
    list_my_transfer_notices,
    list_notices_for_user,
    read_my_transfer_notice,
)
from app.device_transfer_store import issue_reset_challenge, stage_verified_transfer
from app.factory_reset_proof import reset_proof_message
from app.models import Device, DeviceMembership, DeviceTransferNotice, User


UID = "SM-A1B2C3D4E5F6"
KEY = bytes(range(32))
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class TransferInboxTests(unittest.TestCase):
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
        device = Device(device_uid=UID, mqtt_device_id="bench", name="Banc")
        self.db.add_all((old_owner, new_owner, device))
        self.db.flush()
        self.old_id = old_owner.id
        self.new_id = new_owner.id
        self.db.add(
            DeviceMembership(user_id=old_owner.id, device_id=device.id, role="owner")
        )
        self.db.commit()
        challenge = issue_reset_challenge(self.db, device_uid=UID, now=NOW)
        tag = hmac.new(
            KEY,
            reset_proof_message(UID, 1, challenge.challenge_hex),
            hashlib.sha256,
        ).hexdigest().upper()
        transfer = stage_verified_transfer(
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
        self.notice_id = self.db.query(DeviceTransferNotice).filter_by(
            transfer_id=transfer.id
        ).one().id
    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_only_notice_owner_can_list_or_mark_read(self):
        new_owner = self.db.get(User, self.new_id)
        old_owner = self.db.get(User, self.old_id)
        self.assertEqual(
            list_my_transfer_notices(
                current_user=new_owner, db=self.db, limit=100
            ),
            [],
        )
        with self.assertRaises(HTTPException) as error:
            read_my_transfer_notice(
                self.notice_id, current_user=new_owner, db=self.db
            )
        self.assertEqual(error.exception.status_code, 404)
        self.assertIsNone(self.db.get(DeviceTransferNotice, self.notice_id).read_at)

        notices = list_my_transfer_notices(
            current_user=old_owner, db=self.db, limit=100
        )
        self.assertEqual(len(notices), 1)
        self.assertIn("action physique", notices[0].body)
        self.assertIsNone(notices[0].read_at)
        notice = read_my_transfer_notice(
            self.notice_id, current_user=old_owner, db=self.db
        )
        self.assertIsNotNone(notice.read_at)
        first_read_at = notice.read_at
        notice = read_my_transfer_notice(
            self.notice_id, current_user=old_owner, db=self.db
        )
        self.assertEqual(notice.read_at, first_read_at)

    def test_limit_and_unknown_notice_are_rejected(self):
        with self.assertRaises(ValueError):
            list_notices_for_user(self.db, user_id=self.old_id, limit=0)
        with self.assertRaises(HTTPException) as error:
            read_my_transfer_notice(
                99999, current_user=self.db.get(User, self.old_id), db=self.db
            )
        self.assertEqual(error.exception.status_code, 404)
