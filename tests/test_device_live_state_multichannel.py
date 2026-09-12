import unittest

from app.device_live_state import (
    DeviceLiveStateStore,
)


class DeviceLiveStateMultichannelTests(
    unittest.TestCase
):
    def test_v2_keeps_all_channels_and_power1_compatibility(
        self,
    ):
        store = DeviceLiveStateStore()

        store.update(
            mqtt_device_id="atelier",
            payload={
                "schema_version": 2,
                "device": "atelier",
                "timestamp": 0,
                "system": {
                    "ntp_synchronized": False,
                },
                "managers": {
                    "sensor": {
                        "status": "OK",
                    },
                    "energy": {
                        "status": "OK",
                    },
                },
                "environment": {},
                "energy_channels": {
                    "power_1": {
                        "voltage_v": 230.0,
                        "current_a": 1.0,
                        "power_w": 230.0,
                        "energy_kwh": 1.0,
                        "frequency_hz": 50.0,
                        "power_factor": 0.98,
                        "freshness": "fresh",
                    },
                    "power_2": {
                        "voltage_v": 231.0,
                        "current_a": 2.0,
                        "power_w": 462.0,
                        "energy_kwh": 2.0,
                        "frequency_hz": 50.0,
                        "power_factor": 0.97,
                        "freshness": "fresh",
                    },
                },
            },
        )

        snapshot = store.get(
            "atelier"
        )

        self.assertIsNotNone(
            snapshot
        )

        self.assertEqual(
            snapshot[
                "energy_channels"
            ][
                "power_2"
            ][
                "power_w"
            ],
            462.0,
        )

        self.assertEqual(
            snapshot[
                "power_w"
            ],
            230.0,
        )

    def test_v1_is_exposed_as_power1(
        self,
    ):
        store = DeviceLiveStateStore()

        store.update(
            mqtt_device_id="atelier",
            payload={
                "schema_version": 1,
                "device": "atelier",
                "timestamp": 0,
                "system": {
                    "ntp_synchronized": False,
                },
                "managers": {},
                "environment": {},
                "energy": {
                    "voltage_v": 230.0,
                    "current_a": 1.0,
                    "power_w": 230.0,
                },
            },
        )

        snapshot = store.get(
            "atelier"
        )

        self.assertIn(
            "power_1",
            snapshot[
                "energy_channels"
            ],
        )

        self.assertEqual(
            snapshot[
                "power_w"
            ],
            230.0,
        )


if __name__ == "__main__":
    unittest.main()
