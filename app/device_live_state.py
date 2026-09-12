import math
import threading

from copy import deepcopy
from datetime import datetime, timezone


POWER_CHANNEL_KEYS = (
    "power_1",
    "power_2",
    "power_3",
    "power_4",
)

LIVE_VALUE_HOLD_SECONDS = 15.0

ENERGY_VALUE_FIELDS = (
    "voltage_v",
    "current_a",
    "power_w",
    "energy_kwh",
    "frequency_hz",
    "power_factor",
)


class DeviceLiveStateStore:
    """
    Dernier snapshot MQTT /state reçu pour chaque SmartMonitor.

    Ce cache est volontairement indépendant des règles de
    persistance historique : l'application peut ainsi afficher
    l'état courant même si NTP n'est pas encore synchronisé ou
    si un manager est momentanément dégradé.
    """

    def __init__(self, clock=None):
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {}
        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )
        self._last_values: dict[
            str,
            dict[str, tuple[float, datetime]],
        ] = {}

    def _retain_recent_values(
        self,
        *,
        mqtt_device_id: str,
        snapshot: dict,
        received_at: datetime,
    ) -> None:
        cached_values = self._last_values.setdefault(
            mqtt_device_id,
            {},
        )

        def retain(field_key: str, value):
            if value is not None:
                cached_values[field_key] = (
                    value,
                    received_at,
                )
                return value, 0

            cached = cached_values.get(field_key)
            if cached is None:
                return None, None

            cached_value, cached_at = cached
            age_seconds = (
                received_at - cached_at
            ).total_seconds()

            if (
                age_seconds < 0
                or age_seconds
                > LIVE_VALUE_HOLD_SECONDS
            ):
                cached_values.pop(
                    field_key,
                    None,
                )
                return None, None

            return (
                cached_value,
                int(age_seconds * 1000),
            )

        sensor_fallback_ages = []

        for field_name in (
            "temperature_c",
            "humidity_pct",
        ):
            value, fallback_age_ms = retain(
                f"sensor.{field_name}",
                snapshot.get(field_name),
            )
            snapshot[field_name] = value

            if fallback_age_ms is not None:
                sensor_fallback_ages.append(
                    fallback_age_ms
                )

        if sensor_fallback_ages:
            snapshot["sensor_freshness"] = "stale"
            snapshot["sensor_age_ms"] = max(
                sensor_fallback_ages
            )

        energy_channels = dict(
            snapshot.get("energy_channels") or {}
        )

        for channel_key in POWER_CHANNEL_KEYS:
            channel = dict(
                energy_channels.get(channel_key)
                or {}
            )
            fallback_ages = []

            for field_name in ENERGY_VALUE_FIELDS:
                value, fallback_age_ms = retain(
                    f"{channel_key}.{field_name}",
                    channel.get(field_name),
                )
                channel[field_name] = value

                if fallback_age_ms is not None:
                    fallback_ages.append(
                        fallback_age_ms
                    )

            if any(
                channel.get(field_name) is not None
                for field_name in ENERGY_VALUE_FIELDS
            ):
                if fallback_ages:
                    channel["freshness"] = "stale"
                    channel["age_ms"] = max(
                        fallback_ages
                    )

                energy_channels[channel_key] = channel

        snapshot["energy_channels"] = (
            energy_channels or None
        )

        primary_energy = energy_channels.get(
            "power_1",
            {},
        )

        for field_name in ENERGY_VALUE_FIELDS:
            snapshot[field_name] = (
                primary_energy.get(field_name)
            )

        snapshot["energy_freshness"] = (
            primary_energy.get("freshness")
        )
        snapshot["energy_age_ms"] = (
            primary_energy.get("age_ms")
        )

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

    def _normalize_energy_block(
        self,
        energy: dict,
    ) -> dict:
        return {
            "freshness":
                self._measurement_freshness(
                    energy.get(
                        "freshness"
                    )
                ),

            "age_ms":
                self._non_negative_int(
                    energy.get(
                        "age_ms"
                    )
                ),

            "voltage_v":
                self._finite_float(
                    energy.get(
                        "voltage_v"
                    )
                ),

            "voltage_quality":
                self._measurement_quality(
                    energy.get(
                        "voltage_quality"
                    )
                ),

            "current_a":
                self._finite_float(
                    energy.get(
                        "current_a"
                    )
                ),

            "current_quality":
                self._measurement_quality(
                    energy.get(
                        "current_quality"
                    )
                ),

            "power_w":
                self._finite_float(
                    energy.get(
                        "power_w"
                    )
                ),

            "power_quality":
                self._measurement_quality(
                    energy.get(
                        "power_quality"
                    )
                ),

            "energy_kwh":
                self._finite_float(
                    energy.get(
                        "energy_kwh"
                    )
                ),

            "energy_quality":
                self._measurement_quality(
                    energy.get(
                        "energy_quality"
                    )
                ),

            "frequency_hz":
                self._finite_float(
                    energy.get(
                        "frequency_hz"
                    )
                ),

            "frequency_quality":
                self._measurement_quality(
                    energy.get(
                        "frequency_quality"
                    )
                ),

            "power_factor":
                self._finite_float(
                    energy.get(
                        "power_factor"
                    )
                ),

            "power_factor_quality":
                self._measurement_quality(
                    energy.get(
                        "power_factor_quality"
                    )
                ),
        }

    def update(
        self,
        *,
        mqtt_device_id: str,
        payload: dict,
    ) -> None:
        received_at = self._clock()

        environment = payload.get("environment")
        if not isinstance(environment, dict):
            environment = {}

        schema_version = payload.get(
            "schema_version"
        )

        raw_energy_channels = {}

        if schema_version == 2:
            candidate = payload.get(
                "energy_channels"
            )

            if isinstance(
                candidate,
                dict,
            ):
                raw_energy_channels = (
                    candidate
                )

        else:
            legacy_energy = payload.get(
                "energy"
            )

            if isinstance(
                legacy_energy,
                dict,
            ):
                raw_energy_channels = {
                    "power_1":
                        legacy_energy,
                }

        energy_channels = {}

        for channel_key in (
            POWER_CHANNEL_KEYS
        ):
            energy_block = (
                raw_energy_channels.get(
                    channel_key
                )
            )

            if not isinstance(
                energy_block,
                dict,
            ):
                continue

            energy_channels[
                channel_key
            ] = self._normalize_energy_block(
                energy_block
            )

        primary_energy = (
            energy_channels.get(
                "power_1",
                {},
            )
        )

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

            "device_uid": self._text(payload.get("device_uid")),
            "mqtt_device_id": self._text(payload.get("mqtt_device_id")),
            "model": self._text(payload.get("model")),
            "hardware_revision": self._text(
                payload.get("hardware_revision")
            ),
            "firmware_version": self._text(
                payload.get("firmware_version")
            ),
            "capabilities": (
                payload.get("capabilities")
                if isinstance(payload.get("capabilities"), dict)
                else None
            ),

            "ntp_synchronized": ntp_synchronized,

            "energy_channels": (
                energy_channels
                if energy_channels
                else None
            ),

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
                primary_energy.get("freshness")
            ),
            "energy_age_ms": self._non_negative_int(
                primary_energy.get("age_ms")
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
                primary_energy.get("voltage_v")
            ),
            "voltage_quality": self._measurement_quality(
                primary_energy.get("voltage_quality")
            ),
            "current_a": self._finite_float(
                primary_energy.get("current_a")
            ),
            "current_quality": self._measurement_quality(
                primary_energy.get("current_quality")
            ),
            "power_w": self._finite_float(
                primary_energy.get("power_w")
            ),
            "power_quality": self._measurement_quality(
                primary_energy.get("power_quality")
            ),
            "energy_kwh": self._finite_float(
                primary_energy.get("energy_kwh")
            ),
            "energy_quality": self._measurement_quality(
                primary_energy.get("energy_quality")
            ),
            "frequency_hz": self._finite_float(
                primary_energy.get("frequency_hz")
            ),
            "frequency_quality": self._measurement_quality(
                primary_energy.get("frequency_quality")
            ),
            "power_factor": self._finite_float(
                primary_energy.get("power_factor")
            ),
            "power_factor_quality": self._measurement_quality(
                primary_energy.get("power_factor_quality")
            ),
        }

        with self._lock:
            self._retain_recent_values(
                mqtt_device_id=mqtt_device_id,
                snapshot=snapshot,
                received_at=received_at,
            )
            self._states[mqtt_device_id] = snapshot

    def get(
        self,
        mqtt_device_id: str,
    ) -> dict | None:
        with self._lock:
            snapshot = self._states.get(mqtt_device_id)

            if snapshot is None:
                return None

            return deepcopy(snapshot)


live_state_store = DeviceLiveStateStore()
