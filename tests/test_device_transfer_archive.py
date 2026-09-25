import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.device_transfer_archive import (
    former_owner_window,
    list_former_owner_archives,
)
from app.models import (
    Device,
    DeviceChannel,
    DeviceResetChallenge,
    DeviceTransfer,
    PowerMeasurement,
    PowerMeasurementAttribution,
    User,
)


START = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class FormerOwnerArchiveTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.users = [
            User(
                email=f"owner-{index}@example.test",
                password_hash="unused",
                email_verified=True,
            )
            for index in range(4)
        ]
        self.device = Device(
            device_uid="SM-A1B2C3D4E5F6",
            mqtt_device_id="bench",
            created_at=START,
        )
        self.db.add_all([*self.users, self.device])
        self.db.commit()
        self.channel = DeviceChannel(device_id=self.device.id, channel_key="power_1")
        self.db.add(self.channel)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_completed(self, previous: int, next_: int, hours: int) -> DeviceTransfer:
        effective = START + timedelta(hours=hours)
        challenge = DeviceResetChallenge(
            id=f"challenge-{hours}",
            device_id=self.device.id,
            challenge_digest=f"digest-{hours}",
            created_at=effective - timedelta(minutes=2),
            expires_at=effective + timedelta(minutes=2),
            consumed_at=effective - timedelta(minutes=1),
        )
        self.db.add(challenge)
        transfer = DeviceTransfer(
            device_id=self.device.id,
            previous_owner_user_id=self.users[previous].id,
            next_owner_user_id=self.users[next_].id,
            challenge_id=challenge.id,
            reset_generation=hours,
            state="completed",
            cause="physical_factory_reset_reassociation",
            requested_at=effective - timedelta(minutes=1),
            not_before=effective,
            effective_at=effective,
        )
        self.db.add(transfer)
        self.db.commit()
        return transfer

    def window(self, user: int, start_hour: int, end_hour: int):
        return former_owner_window(
            self.db,
            device_id=self.device.id,
            user_id=self.users[user].id,
            period_from=START + timedelta(hours=start_hour),
            period_to=START + timedelta(hours=end_hour),
        )

    def test_each_former_owner_sees_only_their_epoch_without_pending_minute(self):
        first = self.add_completed(0, 1, 2)
        self.add_completed(1, 2, 5)
        self.assertEqual(
            self.window(0, 0, 4),
            (START, START + timedelta(hours=2, minutes=-1), None),
        )
        self.assertEqual(
            self.window(1, 1, 6),
            (
                START + timedelta(hours=2),
                START + timedelta(hours=5, minutes=-1),
                first.id,
            ),
        )
        for user in (2, 3):
            with self.assertRaises(HTTPException) as error:
                self.window(user, 0, 6)
            self.assertEqual(error.exception.status_code, 404)

    def test_inconsistent_chain_fails_closed(self):
        self.add_completed(0, 1, 2)
        self.add_completed(0, 2, 5)
        with self.assertRaises(HTTPException) as error:
            self.window(0, 0, 6)
        self.assertEqual(error.exception.status_code, 409)

    def test_request_spanning_two_separate_ownership_epochs_is_denied(self):
        self.add_completed(0, 1, 2)
        self.add_completed(1, 2, 5)
        self.add_completed(2, 0, 7)
        self.add_completed(0, 1, 9)
        with self.assertRaises(HTTPException) as error:
            self.window(0, 0, 10)
        self.assertEqual(error.exception.status_code, 404)

    def test_archive_index_is_private_and_separate_from_current_owner(self):
        first = self.add_completed(0, 1, 2)
        second = self.add_completed(1, 2, 5)
        old_measurement = PowerMeasurement(
            channel_id=self.channel.id, measured_at=START + timedelta(hours=1)
        )
        next_measurement = PowerMeasurement(
            channel_id=self.channel.id, measured_at=START + timedelta(hours=3)
        )
        self.db.add_all([old_measurement, next_measurement])
        self.db.flush()
        self.db.add_all([
            PowerMeasurementAttribution(
                measurement_id=old_measurement.id,
                device_id=self.device.id,
                owner_user_id=self.users[0].id,
                epoch_transfer_id=None,
                attributed_at=START + timedelta(hours=1),
            ),
            PowerMeasurementAttribution(
                measurement_id=next_measurement.id,
                device_id=self.device.id,
                owner_user_id=self.users[1].id,
                epoch_transfer_id=first.id,
                attributed_at=START + timedelta(hours=3),
            ),
        ])
        self.db.commit()
        first_list = list_former_owner_archives(self.db, user_id=self.users[0].id)
        second_list = list_former_owner_archives(self.db, user_id=self.users[1].id)
        current_list = list_former_owner_archives(self.db, user_id=self.users[2].id)
        stranger_list = list_former_owner_archives(self.db, user_id=self.users[3].id)
        self.assertEqual([row.archive_id for row in first_list], [first.id])
        self.assertEqual([row.archive_id for row in second_list], [second.id])
        self.assertEqual(first_list[0].channel_ids, [self.channel.id])
        self.assertEqual(second_list[0].channel_ids, [self.channel.id])
        self.assertEqual(first_list[0].period_to, first.requested_at)
        self.assertEqual(second_list[0].period_from, first.effective_at)
        self.assertEqual(current_list, [])
        self.assertEqual(stranger_list, [])

    def test_archive_index_rejects_inconsistent_transfer_chain(self):
        self.add_completed(0, 1, 2)
        self.add_completed(0, 2, 5)
        with self.assertRaises(HTTPException) as error:
            list_former_owner_archives(self.db, user_id=self.users[0].id)
        self.assertEqual(error.exception.status_code, 409)
