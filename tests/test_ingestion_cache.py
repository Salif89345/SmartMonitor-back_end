import json
import unittest

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.ingestion_cache import (
    PowerChannelState,
    PowerIngestionCache,
)
from app.mqtt_client import MqttManager


NOW = datetime.now(timezone.utc)


class Message:
    topic = "smartmonitor/atelier/state"

    def __init__(self, payload: dict):
        self.payload = json.dumps(
            payload
        ).encode("utf-8")


def energy_payload(
    *,
    voltage_v: float,
    current_a: float,
    power_w: float,
    energy_kwh: float,
    power_factor: float,
) -> dict:
    return {
        "freshness": "fresh",
        "voltage_v": voltage_v,
        "voltage_quality": "ok",
        "current_a": current_a,
        "current_quality": "ok",
        "power_w": power_w,
        "power_quality": "ok",
        "energy_kwh": energy_kwh,
        "energy_quality": "ok",
        "frequency_hz": 50.0,
        "frequency_quality": "ok",
        "power_factor": power_factor,
        "power_factor_quality": "ok",
    }


def state_payload(
    timestamp: int,
) -> dict:
    return {
        "schema_version": 1,
        "device": "atelier",
        "timestamp": timestamp,
        "system": {
            "ntp_synchronized": True
        },
        "managers": {
            "energy": {
                "status": "OK"
            }
        },
        "energy": energy_payload(
            voltage_v=230.0,
            current_a=0.2,
            power_w=46.0,
            energy_kwh=1.0,
            power_factor=0.9,
        ),
    }


def state_payload_v2(
    timestamp: int,
) -> dict:
    return {
        "schema_version": 2,
        "device": "atelier",
        "timestamp": timestamp,
        "system": {
            "ntp_synchronized": True
        },
        "managers": {
            "energy": {
                "status": "OK"
            }
        },
        "energy_channels": {
            "power_1": energy_payload(
                voltage_v=230.0,
                current_a=0.2,
                power_w=46.0,
                energy_kwh=1.0,
                power_factor=0.9,
            ),
            "power_2": energy_payload(
                voltage_v=231.0,
                current_a=0.4,
                power_w=92.0,
                energy_kwh=2.0,
                power_factor=0.95,
            ),
        },
    }


class PowerIngestionCacheTests(
    unittest.TestCase
):
    def test_cache_keeps_channel_and_latest_measurement(
        self,
    ):
        cache = PowerIngestionCache()

        initial = PowerChannelState(
            channel_id=7,
            last_measured_at=NOW,
        )

        self.assertEqual(
            cache.set_if_absent(
                "atelier",
                "power_1",
                initial,
            ),
            initial,
        )

        later = NOW + timedelta(
            seconds=60
        )

        cache.record_measurement(
            "atelier",
            "power_1",
            7,
            later,
        )

        self.assertEqual(
            cache.get(
                "atelier",
                "power_1",
            ),
            PowerChannelState(
                channel_id=7,
                last_measured_at=later,
            ),
        )

    def test_channels_are_independent(
        self,
    ):
        cache = PowerIngestionCache()

        cache.set_if_absent(
            "atelier",
            "power_1",
            PowerChannelState(
                channel_id=7,
                last_measured_at=NOW,
            ),
        )

        cache.set_if_absent(
            "atelier",
            "power_2",
            PowerChannelState(
                channel_id=8,
                last_measured_at=None,
            ),
        )

        self.assertEqual(
            cache.get(
                "atelier",
                "power_1",
            ).channel_id,
            7,
        )

        self.assertEqual(
            cache.get(
                "atelier",
                "power_2",
            ).channel_id,
            8,
        )

    def test_existing_state_is_not_overwritten_by_concurrent_load(
        self,
    ):
        cache = PowerIngestionCache()

        current = PowerChannelState(
            channel_id=7,
            last_measured_at=NOW,
        )

        cache.set_if_absent(
            "atelier",
            "power_1",
            current,
        )

        returned = cache.set_if_absent(
            "atelier",
            "power_1",
            PowerChannelState(
                channel_id=9,
                last_measured_at=None,
            ),
        )

        self.assertEqual(
            returned,
            current,
        )

    def test_older_timestamp_cannot_move_cache_backwards(
        self,
    ):
        cache = PowerIngestionCache()

        cache.set_if_absent(
            "atelier",
            "power_1",
            PowerChannelState(
                channel_id=7,
                last_measured_at=NOW,
            ),
        )

        cache.record_measurement(
            "atelier",
            "power_1",
            7,
            NOW - timedelta(
                seconds=1
            ),
        )

        self.assertEqual(
            cache.get(
                "atelier",
                "power_1",
            ).last_measured_at,
            NOW,
        )

    def test_manager_loads_database_once_then_uses_memory(
        self,
    ):
        db = MagicMock()
        db.scalar.side_effect = [
            7,
            NOW,
        ]

        manager = MqttManager()

        with patch(
            "app.mqtt_client.SessionLocal",
            return_value=db,
        ) as session_local:
            first = (
                manager
                ._get_power_channel_state(
                    "atelier",
                    "power_1",
                )
            )

            second = (
                manager
                ._get_power_channel_state(
                    "atelier",
                    "power_1",
                )
            )

        self.assertEqual(
            first,
            second,
        )

        self.assertEqual(
            first.channel_id,
            7,
        )

        self.assertEqual(
            session_local.call_count,
            1,
        )

        self.assertEqual(
            db.scalar.call_count,
            2,
        )

        db.close.assert_called_once()

    def test_different_channels_have_separate_database_cache(
        self,
    ):
        db1 = MagicMock()
        db1.scalar.side_effect = [
            7,
            NOW,
        ]

        db2 = MagicMock()
        db2.scalar.side_effect = [
            8,
            NOW,
        ]

        manager = MqttManager()

        with patch(
            "app.mqtt_client.SessionLocal",
            side_effect=[
                db1,
                db2,
            ],
        ) as session_local:
            power_1 = (
                manager
                ._get_power_channel_state(
                    "atelier",
                    "power_1",
                )
            )

            power_2 = (
                manager
                ._get_power_channel_state(
                    "atelier",
                    "power_2",
                )
            )

        self.assertEqual(
            power_1.channel_id,
            7,
        )

        self.assertEqual(
            power_2.channel_id,
            8,
        )

        self.assertEqual(
            session_local.call_count,
            2,
        )

    def test_manager_reloads_cache_after_restart(
        self,
    ):
        first_db = MagicMock()
        first_db.scalar.side_effect = [
            7,
            NOW,
        ]

        second_db = MagicMock()
        second_db.scalar.side_effect = [
            7,
            NOW,
        ]

        with patch(
            "app.mqtt_client.SessionLocal",
            side_effect=[
                first_db,
                second_db,
            ],
        ) as session_local:
            MqttManager()._get_power_channel_state(
                "atelier",
                "power_1",
            )

            MqttManager()._get_power_channel_state(
                "atelier",
                "power_1",
            )

        self.assertEqual(
            session_local.call_count,
            2,
        )

    def test_state_inside_interval_does_not_read_database(
        self,
    ):
        timestamp = int(
            NOW.timestamp()
        )

        manager = MqttManager()

        manager._power_ingestion_cache.set_if_absent(
            "atelier",
            "power_1",
            PowerChannelState(
                channel_id=7,
                last_measured_at=(
                    datetime.fromtimestamp(
                        timestamp,
                        tz=timezone.utc,
                    )
                ),
            ),
        )

        with patch(
            "app.mqtt_client.SessionLocal"
        ) as session_local:
            manager._handle_state_message(
                Message(
                    state_payload(
                        timestamp + 2
                    )
                )
            )

        session_local.assert_not_called()

    def test_schema_v2_dispatches_each_channel_independently(
        self,
    ):
        timestamp = int(
            NOW.timestamp()
        )

        manager = MqttManager()

        manager._persist_power_channel_measurement = (
            MagicMock()
        )

        manager._handle_state_message(
            Message(
                state_payload_v2(
                    timestamp
                )
            )
        )

        calls = (
            manager
            ._persist_power_channel_measurement
            .call_args_list
        )

        self.assertEqual(
            len(calls),
            2,
        )

        self.assertEqual(
            calls[0].kwargs[
                "channel_key"
            ],
            "power_1",
        )

        self.assertEqual(
            calls[1].kwargs[
                "channel_key"
            ],
            "power_2",
        )


if __name__ == "__main__":
    unittest.main()
