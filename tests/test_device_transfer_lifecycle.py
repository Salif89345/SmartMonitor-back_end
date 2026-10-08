"""One complete SM-015-SEC lifecycle on an isolated test database.

The factory key and broker verifier are test doubles. This test proves the
business transaction, not a live broker or production key-custody system.
"""

import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.devices import claim_device
from app.device_transfer_api import (
    TransferChallengeRequest,
    TransferStageRequest,
    create_transfer_challenge,
    create_transfer_request,
    get_transfer_request,
)
from app.device_transfer_archive import former_owner_window
from app.device_transfer_finalizer import complete_due_transfer
from app.device_transfer_guard import (
    active_transfer_access_allowed,
    require_active_transfer_access,
)
from app.device_transfer_inbox import list_notices_for_user
from app.device_transfer_store import (
    TransferRejected,
    issue_reset_challenge,
    stage_verified_transfer,
)
from app.factory_reset_proof import reset_proof_message
from app.models import (
    Device,
    DeviceMembership,
    DeviceTransferMqttIdentityCheck,
    User,
)
from app.schemas import DeviceClaimRequest


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
UID = "SM-A1B2C3D4E5F6"
FACTORY_KEY = bytes(range(32))


class DeviceTransferLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stage_enabled = patch(
            "app.device_transfer_store.SM015_TRANSFER_STAGING_ENABLED", True
        )
        self.guard_enabled = patch(
            "app.device_transfer_guard.SM015_TRANSFER_STAGING_ENABLED", True
        )
        self.finalizer_enabled = patch(
            "app.device_transfer_finalizer.SM015_TRANSFER_STAGING_ENABLED", True
        )
        self.api_enabled = patch(
            "app.device_transfer_api.SM015_TRANSFER_STAGING_ENABLED", True
        )
        for flag in (
            self.stage_enabled,
            self.guard_enabled,
            self.finalizer_enabled,
            self.api_enabled,
        ):
            flag.start()
            self.addCleanup(flag.stop)

        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        old = User(
            email="previous@example.test",
            password_hash="unused",
            email_verified=True,
        )
        new = User(
            email="next@example.test",
            password_hash="unused",
            email_verified=True,
        )
        device = Device(
            device_uid=UID,
            mqtt_device_id="sm015-bench",
            name="Atelier",
            created_at=NOW - timedelta(days=1),
        )
        self.db.add_all((old, new, device))
        self.db.flush()
        self.old_id, self.new_id, self.device_id = old.id, new.id, device.id
        self.db.add(
            DeviceMembership(device_id=device.id, user_id=old.id, role="owner")
        )
        self.db.commit()

    def test_two_accounts_reset_notice_delay_transfer_and_archive(self):
        challenge = issue_reset_challenge(self.db, device_uid=UID, now=NOW)
        proof_tag = hmac.new(
            FACTORY_KEY,
            reset_proof_message(UID, 1, challenge.challenge_hex),
            hashlib.sha256,
        ).hexdigest().upper()
        request = dict(
            device_uid=UID,
            next_owner_user_id=self.new_id,
            reset_generation=1,
            challenge_id=challenge.id,
            challenge_hex=challenge.challenge_hex,
            tag_hex=proof_tag,
            factory_key=FACTORY_KEY,
            now=NOW,
        )

        with self.assertRaisesRegex(TransferRejected, "physical reset proof invalid"):
            stage_verified_transfer(self.db, **{**request, "tag_hex": "0" * 64})
        transfer = stage_verified_transfer(self.db, **request)
        self.assertEqual(transfer.state, "pending")
        self.assertEqual(transfer.not_before, NOW + timedelta(minutes=1))
        self.assertEqual(
            [
                (notice.user_id, notice.phase)
                for notice in list_notices_for_user(self.db, user_id=self.old_id)
            ],
            [(self.old_id, "pending")],
        )
        self.assertFalse(
            active_transfer_access_allowed(
                self.db, device_id=self.device_id, user_id=self.old_id, role="owner"
            )
        )
        with self.assertRaises(HTTPException) as blocked:
            require_active_transfer_access(
                self.db, device_id=self.device_id, user_id=self.old_id, role="owner"
            )
        self.assertEqual(blocked.exception.status_code, 409)

        verifier = Mock()
        verifier.verify.return_value = True
        with self.assertRaisesRegex(TransferRejected, "delay not elapsed"):
            complete_due_transfer(
                self.db,
                transfer_id=transfer.id,
                identity_verifier=verifier,
                now=NOW + timedelta(seconds=59),
            )
        self.assertEqual(transfer.state, "pending")

        self.db.add(
            DeviceTransferMqttIdentityCheck(
                transfer_id=transfer.id,
                device_uid=UID,
                mqtt_device_id="sm015-bench",
                broker_reference="isolated-test-only",
                verified_at=NOW + timedelta(seconds=30),
            )
        )
        self.db.commit()
        complete_due_transfer(
            self.db,
            transfer_id=transfer.id,
            identity_verifier=verifier,
            now=NOW + timedelta(minutes=1),
        )
        verifier.verify.assert_called_once_with(
            device_uid=UID,
            mqtt_device_id="sm015-bench",
            broker_reference="isolated-test-only",
        )
        owners = list(
            self.db.scalars(
                select(DeviceMembership).where(DeviceMembership.device_id == self.device_id)
            ).all()
        )
        self.assertEqual(
            [(owner.user_id, owner.role) for owner in owners],
            [(self.new_id, "owner")],
        )
        self.assertFalse(
            active_transfer_access_allowed(
                self.db, device_id=self.device_id, user_id=self.old_id, role="owner"
            )
        )
        self.assertTrue(
            active_transfer_access_allowed(
                self.db, device_id=self.device_id, user_id=self.new_id, role="owner"
            )
        )
        old_notices = list_notices_for_user(self.db, user_id=self.old_id)
        self.assertEqual([notice.phase for notice in old_notices], ["completed", "pending"])
        self.assertIn("action physique", old_notices[-1].body)
        self.assertEqual(
            [notice.phase for notice in list_notices_for_user(self.db, user_id=self.new_id)],
            ["completed"],
        )
        archived_from, archived_to, archived_epoch = former_owner_window(
            self.db,
            device_id=self.device_id,
            user_id=self.old_id,
            period_from=NOW - timedelta(hours=1),
            period_to=NOW + timedelta(hours=1),
        )
        self.assertEqual(
            (archived_from, archived_to, archived_epoch),
            (NOW - timedelta(hours=1), NOW, None),
        )
        with self.assertRaises(HTTPException) as denied_archive:
            former_owner_window(
                self.db,
                device_id=self.device_id,
                user_id=self.new_id,
                period_from=NOW - timedelta(hours=1),
                period_to=NOW,
            )
        self.assertEqual(denied_archive.exception.status_code, 404)

    def test_authenticated_api_links_mqtt_claim_to_reset_proof_and_request(self):
        new_owner = self.db.get(User, self.new_id)
        old_owner = self.db.get(User, self.old_id)
        factory_keys = Mock()
        factory_keys.key_for_device.return_value = FACTORY_KEY
        with patch("app.device_transfer_api.mqtt_manager") as mqtt:
            mqtt.reserve_claim_proof.return_value = ("reservation-1", "sm015-bench")
            mqtt.commit_claim_proof.return_value = True
            challenge = create_transfer_challenge(
                TransferChallengeRequest(device_uid=UID, nonce="A" * 32),
                current_user=new_owner,
                db=self.db,
                factory_keys=factory_keys,
            )
            mqtt.commit_claim_proof.assert_called_once_with(
                device_uid=UID, reservation_id="reservation-1"
            )
        tag = hmac.new(
            FACTORY_KEY,
            reset_proof_message(UID, 1, challenge.challenge_hex),
            hashlib.sha256,
        ).hexdigest().upper()
        request = TransferStageRequest(
            device_uid=UID,
            challenge_id=challenge.challenge_id,
            challenge_hex=challenge.challenge_hex,
            reset_generation=1,
            tag_hex=tag,
        )
        with self.assertRaises(HTTPException) as wrong_account:
            create_transfer_request(
                request,
                current_user=old_owner,
                db=self.db,
                factory_keys=factory_keys,
            )
        self.assertEqual(wrong_account.exception.status_code, 409)
        staged = create_transfer_request(
            request,
            current_user=new_owner,
            db=self.db,
            factory_keys=factory_keys,
        )
        self.assertEqual(staged.state, "pending")
        self.assertEqual(
            get_transfer_request(
                staged.transfer_id, current_user=new_owner, db=self.db
            ).state,
            "pending",
        )
        with self.assertRaises(HTTPException) as private:
            get_transfer_request(
                staged.transfer_id, current_user=old_owner, db=self.db
            )
        self.assertEqual(private.exception.status_code, 404)

    def test_api_refuses_to_issue_challenge_without_server_side_factory_key(self):
        with patch("app.device_transfer_api.mqtt_manager") as mqtt:
            with self.assertRaises(HTTPException) as unavailable:
                create_transfer_challenge(
                    TransferChallengeRequest(device_uid=UID, nonce="A" * 32),
                    current_user=self.db.get(User, self.new_id),
                    db=self.db,
                    factory_keys=None,
                )
            mqtt.reserve_claim_proof.assert_not_called()
        self.assertEqual(unavailable.exception.status_code, 503)

    def test_existing_owner_claim_redirects_to_separate_transfer_path(self):
        with patch("app.devices.mqtt_manager") as mqtt:
            mqtt.reserve_claim_proof.return_value = ("reservation-2", "sm015-bench")
            with self.assertRaises(HTTPException) as conflict:
                claim_device(
                    DeviceClaimRequest(device_uid=UID, nonce="B" * 32),
                    current_user=self.db.get(User, self.new_id),
                    db=self.db,
                )
            mqtt.release_claim_proof.assert_called_once()
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(conflict.exception.detail, "Device already has an owner")


if __name__ == "__main__":
    unittest.main()
