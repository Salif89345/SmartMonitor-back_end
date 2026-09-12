import unittest

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    patch,
)

from app.devices import (
    LIVE_STATE_FRESH_SECONDS,
    build_device_detail_response,
    build_device_list_response,
    build_device_response,
)


NOW = datetime.now(timezone.utc)


def make_device(
    *,
    mqtt_device_id="four",
):
    return SimpleNamespace(
        id=1,
        device_uid="SM-3C1783AE114C",
        mqtt_device_id=mqtt_device_id,
        name="SmartMonitor Test",
        is_active=True,
        created_at=NOW,
    )


def make_channel(
    *,
    channel_id,
    channel_key,
    enabled=True,
):
    return SimpleNamespace(
        id=channel_id,
        device_id=1,
        channel_key=channel_key,
        name=channel_key,
        is_enabled=enabled,
    )


def make_live_snapshot(
    *,
    received_at=NOW,
):
    return {
        "received_at": received_at,
        "measured_at": None,

        "model": "SmartMonitor",
        "hardware_revision": "A",
        "firmware_version": "0.5.0-dev",

        "ntp_synchronized": False,

        "sensor_status": "FAILED",
        "energy_status": "OK",

        "temperature_c": None,
        "humidity_pct": None,

        "voltage_v": 237.8,
        "current_a": 0.364,
        "power_w": 52.4,
        "energy_kwh": 5.749,
        "frequency_hz": 50.0,
        "power_factor": 0.61,
    }


class DeviceDetailContractTests(
    unittest.TestCase
):
    def test_base_contract_remains_minimal(
        self,
    ):
        response = build_device_response(
            make_device(),
            "owner",
        )

        self.assertEqual(
            response.role,
            "owner",
        )

        self.assertFalse(
            hasattr(
                response,
                "availability",
            )
        )

    def test_list_exposes_availability_and_last_state(
        self,
    ):
        device = make_device()

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=(
                    make_live_snapshot()
                ),
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value="online",
            ),
        ):
            response = (
                build_device_list_response(
                    device,
                    "owner",
                )
            )

        self.assertEqual(
            response.availability,
            "online",
        )

        self.assertEqual(
            response.last_state_received_at,
            NOW,
        )

    def test_recent_state_proves_online_when_mqtt_status_is_temporarily_unknown(
        self,
    ):
        device = make_device()

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=make_live_snapshot(
                    received_at=datetime.now(timezone.utc)
                ),
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value=None,
            ),
        ):
            response = build_device_list_response(
                device,
                "owner",
            )

        self.assertEqual(
            response.availability,
            "online",
        )

    def test_live_snapshot_is_fresh_when_recent(
        self,
    ):
        device = make_device()

        power_channel = make_channel(
            channel_id=7,
            channel_key="power_1",
        )

        db = MagicMock()

        db.scalars.return_value.all.return_value = [
            power_channel
        ]

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=(
                    make_live_snapshot()
                ),
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value="online",
            ),
        ):
            response = (
                build_device_detail_response(
                    device,
                    "owner",
                    db,
                )
            )

        self.assertIsNotNone(
            response.telemetry
        )

        self.assertEqual(
            response.telemetry.source,
            "mqtt",
        )

        self.assertEqual(
            response.telemetry.freshness,
            "fresh",
        )

        self.assertLessEqual(
            response.telemetry.age_seconds,
            LIVE_STATE_FRESH_SECONDS,
        )

    def test_old_mqtt_snapshot_becomes_stale(
        self,
    ):
        device = make_device()

        power_channel = make_channel(
            channel_id=7,
            channel_key="power_1",
        )

        db = MagicMock()

        db.scalars.return_value.all.return_value = [
            power_channel
        ]

        old_received_at = (
            datetime.now(timezone.utc)
            - timedelta(seconds=30)
        )

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=(
                    make_live_snapshot(
                        received_at=old_received_at
                    )
                ),
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value="offline",
            ),
        ):
            response = (
                build_device_detail_response(
                    device,
                    "owner",
                    db,
                )
            )

        self.assertEqual(
            response.availability,
            "offline",
        )

        self.assertEqual(
            response.telemetry.source,
            "mqtt",
        )

        self.assertEqual(
            response.telemetry.freshness,
            "stale",
        )

        self.assertGreater(
            response.telemetry.age_seconds,
            LIVE_STATE_FRESH_SECONDS,
        )

    def test_power_channel_is_selected_by_key_not_order(
        self,
    ):
        device = make_device()

        other_channel = make_channel(
            channel_id=2,
            channel_key="temperature_1",
        )

        power_channel = make_channel(
            channel_id=7,
            channel_key="power_1",
        )

        db = MagicMock()

        db.scalars.return_value.all.return_value = [
            other_channel,
            power_channel,
        ]

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=(
                    make_live_snapshot()
                ),
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value="online",
            ),
        ):
            response = (
                build_device_detail_response(
                    device,
                    "owner",
                    db,
                )
            )

        self.assertEqual(
            response.telemetry_channel_id,
            7,
        )

    def test_persisted_fallback_is_always_stale(
        self,
    ):
        device = make_device()

        power_channel = make_channel(
            channel_id=7,
            channel_key="power_1",
        )

        measurement = SimpleNamespace(
            id=10,
            channel_id=7,
            measured_at=NOW,
            received_at=NOW,
            voltage_v=236.5,
            current_a=0.3,
            power_w=48.2,
            energy_kwh=5.8,
            frequency_hz=50.0,
            power_factor=0.68,
        )

        db = MagicMock()

        db.scalars.return_value.all.return_value = [
            power_channel
        ]

        db.scalar.return_value = measurement

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=None,
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value="offline",
            ),
        ):
            response = (
                build_device_detail_response(
                    device,
                    "member",
                    db,
                )
            )

        self.assertIsNotNone(
            response.telemetry
        )

        self.assertEqual(
            response.telemetry.source,
            "persisted",
        )

        self.assertEqual(
            response.telemetry.freshness,
            "stale",
        )

        self.assertEqual(
            response.telemetry.power_w,
            48.2,
        )

    def test_missing_live_channel_uses_its_latest_persisted_measurement(
        self,
    ):
        device = make_device()
        power_1 = make_channel(
            channel_id=7,
            channel_key="power_1",
        )
        power_2 = make_channel(
            channel_id=8,
            channel_key="power_2",
        )
        measurement = SimpleNamespace(
            id=11,
            channel_id=8,
            measured_at=NOW,
            received_at=NOW,
            voltage_v=230.0,
            current_a=0.5,
            power_w=115.0,
            energy_kwh=12.0,
            frequency_hz=50.0,
            power_factor=1.0,
        )
        db = MagicMock()
        db.scalars.return_value.all.return_value = [
            power_1,
            power_2,
        ]
        db.scalar.return_value = measurement

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=make_live_snapshot(),
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value="online",
            ),
        ):
            response = build_device_detail_response(
                device,
                "owner",
                db,
            )

        power_2_telemetry = (
            response.telemetry.energy_channels[
                "power_2"
            ]
        )
        self.assertEqual(
            power_2_telemetry.power_w,
            115.0,
        )
        self.assertEqual(
            power_2_telemetry.freshness,
            "stale",
        )

    def test_no_live_or_persisted_measurement_returns_null(
        self,
    ):
        device = make_device()

        power_channel = make_channel(
            channel_id=7,
            channel_key="power_1",
        )

        db = MagicMock()

        db.scalars.return_value.all.return_value = [
            power_channel
        ]

        db.scalar.return_value = None

        with (
            patch(
                "app.devices.live_state_store.get",
                return_value=None,
            ),
            patch(
                "app.devices.mqtt_manager.get_device_status",
                return_value=None,
            ),
        ):
            response = (
                build_device_detail_response(
                    device,
                    "owner",
                    db,
                )
            )

        self.assertIsNone(
            response.telemetry
        )

        self.assertEqual(
            response.availability,
            "unknown",
        )

        self.assertIsNone(
            response.last_state_received_at
        )


if __name__ == "__main__":
    unittest.main()
