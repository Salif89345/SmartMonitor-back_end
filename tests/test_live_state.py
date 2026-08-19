import unittest

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


if __name__ == "__main__":
    unittest.main()