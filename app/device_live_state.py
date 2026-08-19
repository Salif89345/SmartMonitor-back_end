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

            "temperature_c": self._finite_float(
                environment.get("temperature_c")
            ),
            "humidity_pct": self._finite_float(
                environment.get("humidity_pct")
            ),

            "voltage_v": self._finite_float(
                energy.get("voltage_v")
            ),
            "current_a": self._finite_float(
                energy.get("current_a")
            ),
            "power_w": self._finite_float(
                energy.get("power_w")
            ),
            "energy_kwh": self._finite_float(
                energy.get("energy_kwh")
            ),
            "frequency_hz": self._finite_float(
                energy.get("frequency_hz")
            ),
            "power_factor": self._finite_float(
                energy.get("power_factor")
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