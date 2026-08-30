import math
import threading

from datetime import datetime, timezone


class DeviceLiveStateStore:
    """
    Dernier snapshot MQTT /state reçu pour chaque SmartMonitor.

    Ce cache est volontairement indépendant des règles de
    persistance historique : l'application peut ainsi afficher
    l'état courant même si NTP n'est pas encore synchronisé ou
    si un manager est momentanément dégradé.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {}

    @staticmethod
    def _finite_float(value) -> float | None:
        if isinstance(value, bool):
            return None

        if not isinstance(value, (int, float)):
            return None

        numeric_value = float(value)

        if not math.isfinite(numeric_value):
            return None

        return numeric_value

    @staticmethod
    def _text(value) -> str | None:
        if not isinstance(value, str):
            return None

        normalized = value.strip()

        return normalized or None

    @staticmethod
    def _measurement_quality(value) -> str | None:
        if not isinstance(value, str):
            return None

        normalized = value.strip().lower()

        if normalized not in {
            "ok",
            "incoherent",
            "unavailable",
        }:
            return None

        return normalized

    @staticmethod
    def _measurement_freshness(value) -> str | None:
        if not isinstance(value, str):
            return None

        normalized = value.strip().lower()

        if normalized not in {
            "unknown",
            "fresh",
            "stale",
        }:
            return None

        return normalized

    @staticmethod
    def _non_negative_int(value) -> int | None:
        if isinstance(value, bool):
            return None

        if not isinstance(value, (int, float)):
            return None

        numeric_value = float(value)

        if (
            not math.isfinite(numeric_value)
            or numeric_value < 0
            or not numeric_value.is_integer()
        ):
            return None

        return int(numeric_value)

    def update(
        self,
        *,
        mqtt_device_id: str,
        payload: dict,
    ) -> None:
        received_at = datetime.now(timezone.utc)

        environment = payload.get("environment")
        if not isinstance(environment, dict):
            environment = {}

        energy = payload.get("energy")
        if not isinstance(energy, dict):
            energy = {}

        system = payload.get("system")
        if not isinstance(system, dict):
            system = {}

        managers = payload.get("managers")
        if not isinstance(managers, dict):
            managers = {}

        sensor_manager = managers.get("sensor")
        if not isinstance(sensor_manager, dict):
            sensor_manager = {}

        energy_manager = managers.get("energy")
        if not isinstance(energy_manager, dict):
            energy_manager = {}

        ntp_synchronized = system.get("ntp_synchronized")

        if not isinstance(ntp_synchronized, bool):
            ntp_synchronized = None

        measured_at = None
        timestamp = self._finite_float(payload.get("timestamp"))

        if (
            ntp_synchronized is True
            and timestamp is not None
            and timestamp > 0
        ):
            try:
                measured_at = datetime.fromtimestamp(
                    timestamp,
                    tz=timezone.utc,
                )
            except (
                OverflowError,
                OSError,
                ValueError,
            ):
                measured_at = None

        snapshot = {
            "received_at": received_at,
            "measured_at": measured_at,

            "model": self._text(payload.get("model")),
            "hardware_revision": self._text(
                payload.get("hardware_revision")
            ),
            "firmware_version": self._text(
                payload.get("firmware_version")
            ),

            "ntp_synchronized": ntp_synchronized,

            "sensor_status": self._text(
                sensor_manager.get("status")
            ),
            "energy_status": self._text(
                energy_manager.get("status")
            ),

            "sensor_freshness": self._measurement_freshness(
                environment.get("freshness")
            ),
            "sensor_age_ms": self._non_negative_int(
                environment.get("age_ms")
            ),

            "energy_freshness": self._measurement_freshness(
                energy.get("freshness")
            ),
            "energy_age_ms": self._non_negative_int(
                energy.get("age_ms")
            ),

            "temperature_c": self._finite_float(
                environment.get("temperature_c")
            ),
            "temperature_quality": self._measurement_quality(
                environment.get("temperature_quality")
            ),
            "humidity_pct": self._finite_float(
                environment.get("humidity_pct")
            ),
            "humidity_quality": self._measurement_quality(
                environment.get("humidity_quality")
            ),

            "voltage_v": self._finite_float(
                energy.get("voltage_v")
            ),
            "voltage_quality": self._measurement_quality(
                energy.get("voltage_quality")
            ),
            "current_a": self._finite_float(
                energy.get("current_a")
            ),
            "current_quality": self._measurement_quality(
                energy.get("current_quality")
            ),
            "power_w": self._finite_float(
                energy.get("power_w")
            ),
            "power_quality": self._measurement_quality(
                energy.get("power_quality")
            ),
            "energy_kwh": self._finite_float(
                energy.get("energy_kwh")
            ),
            "energy_quality": self._measurement_quality(
                energy.get("energy_quality")
            ),
            "frequency_hz": self._finite_float(
                energy.get("frequency_hz")
            ),
            "frequency_quality": self._measurement_quality(
                energy.get("frequency_quality")
            ),
            "power_factor": self._finite_float(
                energy.get("power_factor")
            ),
            "power_factor_quality": self._measurement_quality(
                energy.get("power_factor_quality")
            ),
        }

        with self._lock:
            self._states[mqtt_device_id] = snapshot

    def get(
        self,
        mqtt_device_id: str,
    ) -> dict | None:
        with self._lock:
            snapshot = self._states.get(mqtt_device_id)

            if snapshot is None:
                return None

            return dict(snapshot)


live_state_store = DeviceLiveStateStore()