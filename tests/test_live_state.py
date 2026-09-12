import unittest

from datetime import datetime, timedelta, timezone

from app.device_live_state import DeviceLiveStateStore


MQTT_DEVICE_ID = "four"


def base_payload() -> dict:
    return {
        "schema_version": 1,
        "device": MQTT_DEVICE_ID,
        "model": "SmartMonitor",
        "hardware_revision": "A",
        "firmware_version": "0.5.0-dev",
        "timestamp": 0,
        "environment": {
            "temperature_c": None,
            "humidity_pct": None,
        },
        "energy": {
            "voltage_v": 237.8,
            "current_a": 0.364,
            "power_w": 52.4,
            "energy_kwh": 5.749,
            "frequency_hz": 50.0,
            "power_factor": 0.61,
        },
        "system": {
            "ntp_synchronized": False,
        },
        "managers": {
            "sensor": {
                "status": "FAILED",
            },
            "energy": {
                "status": "OK",
            },
        },
    }


class LiveStateTests(unittest.TestCase):
    def setUp(self):
        self.store = DeviceLiveStateStore()

    def test_snapshot_is_kept_without_ntp(self):
        self.store.update(
            mqtt_device_id=MQTT_DEVICE_ID,
            payload=base_payload(),
        )

        state = self.store.get(MQTT_DEVICE_ID)

        self.assertIsNotNone(state)
        self.assertEqual(state["power_w"], 52.4)
        self.assertEqual(state["voltage_v"], 237.8)
        self.assertFalse(state["ntp_synchronized"])
        self.assertEqual(state["sensor_status"], "FAILED")
        self.assertIsNone(state["measured_at"])
        self.assertIsNotNone(state["received_at"])

    def test_invalid_optional_metric_becomes_unavailable(self):
        payload = base_payload()
        payload["energy"]["frequency_hz"] = "invalid"

        self.store.update(
            mqtt_device_id=MQTT_DEVICE_ID,
            payload=payload,
        )

        state = self.store.get(MQTT_DEVICE_ID)

        self.assertIsNotNone(state)
        self.assertIsNone(state["frequency_hz"])
        self.assertEqual(state["power_w"], 52.4)

    def test_returned_state_is_a_copy(self):
        self.store.update(
            mqtt_device_id=MQTT_DEVICE_ID,
            payload=base_payload(),
        )

        first = self.store.get(MQTT_DEVICE_ID)

        self.assertIsNotNone(first)
        first["power_w"] = 999.0

        second = self.store.get(MQTT_DEVICE_ID)

        self.assertIsNotNone(second)
        self.assertEqual(second["power_w"], 52.4)

    def test_transient_sensor_failure_keeps_recent_values_as_stale(self):
        now = [datetime(2026, 9, 12, tzinfo=timezone.utc)]
        store = DeviceLiveStateStore(
            clock=lambda: now[0]
        )
        valid = base_payload()
        valid["environment"] = {
            "freshness": "fresh",
            "age_ms": 0,
            "temperature_c": 26.5,
            "temperature_quality": "ok",
            "humidity_pct": 36.4,
            "humidity_quality": "ok",
        }
        store.update(
            mqtt_device_id=MQTT_DEVICE_ID,
            payload=valid,
        )

        now[0] += timedelta(seconds=5)
        unavailable = base_payload()
        unavailable["environment"] = {
            "freshness": "unknown",
            "age_ms": None,
            "temperature_c": None,
            "temperature_quality": "unavailable",
            "humidity_pct": None,
            "humidity_quality": "unavailable",
        }
        store.update(
            mqtt_device_id=MQTT_DEVICE_ID,
            payload=unavailable,
        )
        state = store.get(MQTT_DEVICE_ID)

        self.assertEqual(state["temperature_c"], 26.5)
        self.assertEqual(state["humidity_pct"], 36.4)
        self.assertEqual(state["sensor_freshness"], "stale")
        self.assertEqual(state["sensor_age_ms"], 5000)

        now[0] += timedelta(seconds=16)
        store.update(
            mqtt_device_id=MQTT_DEVICE_ID,
            payload=unavailable,
        )
        expired = store.get(MQTT_DEVICE_ID)

        self.assertIsNone(expired["temperature_c"])
        self.assertIsNone(expired["humidity_pct"])


if __name__ == "__main__":
    unittest.main()
