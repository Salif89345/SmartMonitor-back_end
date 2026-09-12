import unittest

from app.measurement_contract import (
    MEASUREMENTS,
    MQTT_STATE_PERIOD_SECONDS,
    POWER_HISTORY_INTERVAL_SECONDS_DEFAULT,
    REQUIRED_ELECTRICAL_FIELDS,
    normalize_electrical_measurement,
)


def valid_energy() -> dict:
    return {
        "freshness": "fresh",
        "voltage_v": 230.1,
        "voltage_quality": "ok",
        "current_a": 1.2,
        "current_quality": "ok",
        "power_w": 275.5,
        "power_quality": "ok",
        "energy_kwh": 42.1,
        "energy_quality": "ok",
        "frequency_hz": 50.0,
        "frequency_quality": "ok",
        "power_factor": 0.99,
        "power_factor_quality": "ok",
    }


class MeasurementContractTests(unittest.TestCase):
    def test_every_v1_indicator_has_an_explicit_contract(self):
        self.assertEqual(
            set(MEASUREMENTS),
            {
                "temperature_c",
                "humidity_pct",
                "voltage_v",
                "current_a",
                "power_w",
                "energy_kwh",
                "frequency_hz",
                "power_factor",
            },
        )

        for key, definition in MEASUREMENTS.items():
            self.assertEqual(definition.key, key)
            self.assertTrue(definition.unit)
            self.assertGreater(
                definition.source_period_seconds,
                0,
            )
            self.assertEqual(
                definition.mqtt_period_seconds,
                MQTT_STATE_PERIOD_SECONDS,
            )
            self.assertTrue(
                definition.missing_value_policy
            )

    def test_electrical_history_contract_is_one_minute(self):
        for key, definition in MEASUREMENTS.items():
            if key in ("temperature_c", "humidity_pct"):
                self.assertIsNone(
                    definition.persistence_period_seconds
                )
                continue

            self.assertEqual(
                definition.persistence_period_seconds,
                POWER_HISTORY_INTERVAL_SECONDS_DEFAULT,
            )

    def test_energy_counter_is_never_averaged(self):
        self.assertEqual(
            MEASUREMENTS["energy_kwh"].aggregations,
            ("first", "last", "delta"),
        )

        for key in (
            "voltage_v",
            "current_a",
            "power_w",
            "frequency_hz",
            "power_factor",
        ):
            self.assertEqual(
                MEASUREMENTS[key].aggregations,
                ("min", "avg", "max"),
            )

    def test_fresh_valid_measurement_is_accepted(self):
        values, reason = normalize_electrical_measurement(
            valid_energy()
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(values)
        self.assertEqual(values["power_w"], 275.5)

    def test_stale_measurement_is_not_historized(self):
        energy = valid_energy()
        energy["freshness"] = "stale"

        values, reason = normalize_electrical_measurement(
            energy
        )

        self.assertIsNone(values)
        self.assertEqual(reason, "not_fresh")

    def test_invalid_core_measurement_rejects_sample(self):
        for field_name in REQUIRED_ELECTRICAL_FIELDS:
            with self.subTest(field_name=field_name):
                energy = valid_energy()
                energy[
                    field_name.replace(
                        "_v", "_quality"
                    ).replace(
                        "_a", "_quality"
                    ).replace(
                        "_w", "_quality"
                    )
                ] = "unavailable"

                values, reason = (
                    normalize_electrical_measurement(
                        energy
                    )
                )

                self.assertIsNone(values)
                self.assertIn(
                    field_name,
                    reason or "",
                )

    def test_invalid_optional_measurement_becomes_null(self):
        energy = valid_energy()
        energy["frequency_quality"] = "incoherent"

        values, reason = normalize_electrical_measurement(
            energy
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(values)
        self.assertIsNone(values["frequency_hz"])
        self.assertEqual(values["power_w"], 275.5)


if __name__ == "__main__":
    unittest.main()
