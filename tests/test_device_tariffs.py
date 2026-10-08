import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.device_tariffs import TariffUpdate, get_device_tariff, set_device_tariff
from app.models import Device, DeviceMembership, DeviceTariff, User


class DeviceTariffTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(email="tariff@example.com", password_hash="test", email_verified=True)
        self.other = User(email="other@example.com", password_hash="test", email_verified=True)
        self.device = Device(device_uid="SM-A1B2C3D4E5F6", mqtt_device_id="atelier")
        self.db.add_all([self.user, self.other, self.device])
        self.db.flush()
        self.db.add(DeviceMembership(user_id=self.user.id, device_id=self.device.id, role="owner"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_price_is_kept_server_side_and_cost_uses_daily_energy(self):
        snapshot = {"daily_energy": {"local_date": 20261005, "energy_kwh": 2.5}}
        with patch("app.device_tariffs.live_state_store.get", return_value=snapshot):
            result = set_device_tariff(
                self.device.id, TariffUpdate(eur_per_kwh=0.25), self.user, self.db
            )
            self.assertEqual(result.estimated_cost_eur, 0.625)
            self.assertEqual(result.local_date, 20261005)
            self.assertEqual(get_device_tariff(self.device.id, self.user, self.db).eur_per_kwh, 0.25)
        self.assertEqual(self.db.get(DeviceTariff, (self.user.id, self.device.id)).eur_per_kwh, 0.25)

    def test_price_is_private_to_user_and_unset_is_explicit(self):
        self.db.add(DeviceMembership(user_id=self.other.id, device_id=self.device.id, role="member"))
        self.db.commit()
        set_device_tariff(self.device.id, TariffUpdate(eur_per_kwh=0.25), self.user, self.db)
        self.assertIsNone(get_device_tariff(self.device.id, self.other, self.db).eur_per_kwh)
        result = set_device_tariff(self.device.id, TariffUpdate(eur_per_kwh=None), self.user, self.db)
        self.assertIsNone(result.eur_per_kwh)
        self.assertIsNone(self.db.get(DeviceTariff, (self.user.id, self.device.id)))

    def test_no_membership_is_denied_and_values_are_bounded(self):
        with self.assertRaises(HTTPException) as missing:
            get_device_tariff(self.device.id, self.other, self.db)
        self.assertEqual(missing.exception.status_code, 404)
        for invalid in (-0.1, 10.1, float("nan"), float("inf")):
            with self.assertRaises(ValidationError):
                TariffUpdate(eur_per_kwh=invalid)
        with self.assertRaises(ValidationError):
            TariffUpdate()
