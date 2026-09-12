"""Contrat de donnees SmartMonitor V1 — SM-035.

Ce module centralise les decisions qui doivent rester coherentes entre
l'ingestion MQTT, l'historisation et les futures interfaces d'export.
Il ne contient aucun etat mutable.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MeasurementDefinition:
    key: str
    unit: str
    source_period_seconds: int
    mqtt_period_seconds: int
    persistence_period_seconds: int | None
    aggregations: tuple[str, ...]
    missing_value_policy: str


MQTT_STATE_PERIOD_SECONDS = 5
POWER_HISTORY_INTERVAL_SECONDS_DEFAULT = 60

POWER_MEASUREMENT_RETENTION_DAYS = 90
POWER_DAILY_SUMMARY_RETENTION_DAYS = 365

DETAILED_HISTORY_MAX_DAYS = 90
HISTORY_MAX_DAYS = 365

HISTORY_TARGET_POINTS_MIN = 60
HISTORY_TARGET_POINTS_DEFAULT = 90
HISTORY_TARGET_POINTS_MAX = 120

HISTORY_RESOLUTION_SECONDS = (
    60,
    120,
    300,
    600,
    900,
    1800,
    3600,
    7200,
    10800,
    21600,
    43200,
    86400,
)

DAILY_RESOLUTION_DAYS = (1,)
HISTORY_TIMEZONE_NAME = "Europe/Paris"


ELECTRICAL_FIELD_QUALITY_KEYS = {
    "voltage_v": "voltage_quality",
    "current_a": "current_quality",
    "power_w": "power_quality",
    "energy_kwh": "energy_quality",
    "frequency_hz": "frequency_quality",
    "power_factor": "power_factor_quality",
}

REQUIRED_ELECTRICAL_FIELDS = (
    "voltage_v",
    "current_a",
    "power_w",
)


MEASUREMENTS = {
    "temperature_c": MeasurementDefinition(
        key="temperature_c",
        unit="degC",
        source_period_seconds=2,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=None,
        aggregations=(),
        missing_value_policy="live_null_no_history_v1",
    ),
    "humidity_pct": MeasurementDefinition(
        key="humidity_pct",
        unit="percent_rh",
        source_period_seconds=2,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=None,
        aggregations=(),
        missing_value_policy="live_null_no_history_v1",
    ),
    "voltage_v": MeasurementDefinition(
        key="voltage_v",
        unit="V",
        source_period_seconds=1,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=(
            POWER_HISTORY_INTERVAL_SECONDS_DEFAULT
        ),
        aggregations=("min", "avg", "max"),
        missing_value_policy="ignore_invalid_no_interpolation",
    ),
    "current_a": MeasurementDefinition(
        key="current_a",
        unit="A",
        source_period_seconds=1,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=(
            POWER_HISTORY_INTERVAL_SECONDS_DEFAULT
        ),
        aggregations=("min", "avg", "max"),
        missing_value_policy="ignore_invalid_no_interpolation",
    ),
    "power_w": MeasurementDefinition(
        key="power_w",
        unit="W",
        source_period_seconds=1,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=(
            POWER_HISTORY_INTERVAL_SECONDS_DEFAULT
        ),
        aggregations=("min", "avg", "max"),
        missing_value_policy="ignore_invalid_no_interpolation",
    ),
    "energy_kwh": MeasurementDefinition(
        key="energy_kwh",
        unit="kWh",
        source_period_seconds=1,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=(
            POWER_HISTORY_INTERVAL_SECONDS_DEFAULT
        ),
        aggregations=("first", "last", "delta"),
        missing_value_policy=(
            "ignore_invalid_and_counter_decrease"
        ),
    ),
    "frequency_hz": MeasurementDefinition(
        key="frequency_hz",
        unit="Hz",
        source_period_seconds=1,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=(
            POWER_HISTORY_INTERVAL_SECONDS_DEFAULT
        ),
        aggregations=("min", "avg", "max"),
        missing_value_policy="ignore_invalid_no_interpolation",
    ),
    "power_factor": MeasurementDefinition(
        key="power_factor",
        unit="1",
        source_period_seconds=1,
        mqtt_period_seconds=MQTT_STATE_PERIOD_SECONDS,
        persistence_period_seconds=(
            POWER_HISTORY_INTERVAL_SECONDS_DEFAULT
        ),
        aggregations=("min", "avg", "max"),
        missing_value_policy="ignore_invalid_no_interpolation",
    ),
}


def normalize_electrical_measurement(
    energy: dict,
) -> tuple[dict[str, float | None] | None, str | None]:
    """Valide une mesure electrique avant historisation.

    Une trame non fraiche n'est jamais historisee. Les trois grandeurs
    centrales doivent etre presentes, finies et de qualite ``ok``.
    Une grandeur optionnelle non valide devient ``None`` afin que les
    agregations SQL l'ignorent sans inventer de valeur.
    """

    if energy.get("freshness") != "fresh":
        return None, "not_fresh"

    values: dict[str, float | None] = {}

    for field_name, quality_key in (
        ELECTRICAL_FIELD_QUALITY_KEYS.items()
    ):
        value = energy.get(field_name)
        quality = energy.get(quality_key)

        if quality != "ok" or value is None:
            values[field_name] = None
            continue

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            return None, f"invalid_{field_name}"

        numeric_value = float(value)

        if not math.isfinite(numeric_value):
            return None, f"invalid_{field_name}"

        values[field_name] = numeric_value

    missing_required = [
        field_name
        for field_name in REQUIRED_ELECTRICAL_FIELDS
        if values[field_name] is None
    ]

    if missing_required:
        return (
            None,
            "missing_or_invalid_core:"
            + ",".join(missing_required),
        )

    return values, None
