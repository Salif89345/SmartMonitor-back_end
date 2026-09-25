import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.commands import _send_owner_command
from app.database import Base
from app.device_transfer_guard import (
    active_transfer_access_allowed,
    bounded_history_period,
    pending_transfer,
    require_active_transfer_access,
)
from app.device_transfer_store import issue_reset_challenge, stage_verified_transfer
from app.devices import get_my_device, list_my_devices
from app.factory_reset_proof import reset_proof_message
from app.models import Device, DeviceMembership, User


UID = "SM-A1B2C3D4E5F6"
KEY = bytes(range(32))
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class TransferGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        old = User(email="old@example.test", password_hash="unused", email_verified=True)
        new = User(email="new@example.test", password_hash="unused", email_verified=True)
        device = Device(device_uid=UID, mqtt_device_id="bench", name="Banc")
        self.db.add_all((old, new, device))
        self.db.flush()
        self.old_id = old.id
        self.new_id = new.id
        self.device_id = device.id
        self.db.add(DeviceMembership(user_id=old.id, device_id=device.id, role="owner"))
        self.db.commit()
        issued = issue_reset_challenge(self.db, device_uid=UID, now=NOW)
        tag = hmac.new(
            KEY,
            reset_proof_message(UID, 1, issued.challenge_hex),
            hashlib.sha256,
        ).hexdigest().upper()
        self.transfer = stage_verified_transfer(
            self.db,
            device_uid=UID,
            next_owner_user_id=self.new_id,
            reset_generation=1,
            challenge_id=issued.id,
            challenge_hex=issued.challenge_hex,
            tag_hex=tag,
            factory_key=KEY,
            now=NOW,
        )
        self.enabled = patch(
            "app.device_transfer_guard.SM015_TRANSFER_STAGING_ENABLED", True
        )
        self.enabled.start()

    def tearDown(self):
        self.enabled.stop()
        self.db.close()
        self.engine.dispose()

    def test_pending_blocks_commands_and_fresh_details(self):
        self.assertIsNotNone(pending_transfer(self.db, self.device_id))
        with self.assertRaises(HTTPException) as error:
            require_active_transfer_access(
                self.db,
                device_id=self.device_id,
                user_id=self.old_id,
                role="owner",
            )
        self.assertEqual(error.exception.status_code, 409)
        old_owner = self.db.get(User, self.old_id)
        with self.assertRaises(HTTPException) as error:
            _send_owner_command(self.device_id, "ping", old_owner, self.db)
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException) as error:
            get_my_device(self.device_id, current_user=old_owner, db=self.db)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(list_my_devices(current_user=old_owner, db=self.db), [])

    def test_history_stops_at_pending_request_and_new_epoch_starts_at_effect(self):
        start, end = bounded_history_period(
            self.db,
            device_id=self.device_id,
            user_id=self.old_id,
            role="owner",
            period_from=NOW - timedelta(hours=1),
            period_to=NOW + timedelta(hours=1),
        )
        self.assertEqual(start, NOW - timedelta(hours=1))
        self.assertEqual(end, NOW)
        with self.assertRaises(HTTPException) as error:
            bounded_history_period(
                self.db,
                device_id=self.device_id,
                user_id=self.old_id,
                role="owner",
                period_from=NOW,
                period_to=NOW + timedelta(hours=1),
            )
        self.assertEqual(error.exception.status_code, 403)

        self.transfer.state = "completed"
        self.transfer.effective_at = NOW + timedelta(minutes=1)
        self.db.commit()
        start, end = bounded_history_period(
            self.db,
            device_id=self.device_id,
            user_id=self.new_id,
            role="owner",
            period_from=NOW - timedelta(hours=1),
            period_to=NOW + timedelta(hours=1),
        )
        self.assertEqual(start, NOW + timedelta(minutes=1))
        self.assertEqual(end, NOW + timedelta(hours=1))
        self.assertFalse(
            active_transfer_access_allowed(
                self.db,
                device_id=self.device_id,
                user_id=self.old_id,
                role="owner",
            )
        )
        self.assertEqual(
            list_my_devices(current_user=self.db.get(User, self.old_id), db=self.db),
            [],
        )
        self.assertTrue(
            active_transfer_access_allowed(
                self.db,
                device_id=self.device_id,
                user_id=self.new_id,
                role="owner",
            )
        )
        with self.assertRaises(HTTPException) as error:
            bounded_history_period(
                self.db,
                device_id=self.device_id,
                user_id=self.old_id,
                role="owner",
                period_from=NOW - timedelta(hours=1),
                period_to=NOW + timedelta(hours=1),
            )
        self.assertEqual(error.exception.status_code, 403)

    def test_disabled_setting_does_not_query_transfer_tables(self):
        with patch("app.device_transfer_guard.SM015_TRANSFER_STAGING_ENABLED", False):
            self.assertIsNone(pending_transfer(self.db, self.device_id))
            self.assertIsNone(
                require_active_transfer_access(
                    self.db,
                    device_id=self.device_id,
                    user_id=self.old_id,
                    role="owner",
                )
            )
            period = bounded_history_period(
                self.db,
                device_id=self.device_id,
                user_id=self.old_id,
                role="owner",
                period_from=NOW,
                period_to=NOW + timedelta(hours=1),
            )
            self.assertEqual(period, (NOW, NOW + timedelta(hours=1)))
