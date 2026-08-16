from datetime import date, datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import PowerDailySummary, PowerMeasurement


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

DAILY_RESOLUTION_DAYS = (
    1,
)

HISTORY_TARGET_POINTS_MIN = 60
HISTORY_TARGET_POINTS_DEFAULT = 90
HISTORY_TARGET_POINTS_MAX = 120

DETAILED_HISTORY_MAX_DAYS = 90
HISTORY_MAX_DAYS = 365

HISTORY_TIMEZONE = ZoneInfo("Europe/Paris")


def choose_history_resolution_seconds(
    period_from: datetime,
    period_to: datetime,
    target_points: int,
) -> int:
    total_seconds = (
        period_to - period_from
    ).total_seconds()

    candidates_in_target_range = []
    all_candidates = []

    for resolution_seconds in (
        HISTORY_RESOLUTION_SECONDS
    ):
        bucket_count = max(
            1,
            ceil(
                total_seconds
                / resolution_seconds
            ),
        )

        candidate = (
            resolution_seconds,
            bucket_count,
        )

        all_candidates.append(candidate)

        if (
            HISTORY_TARGET_POINTS_MIN
            <= bucket_count
            <= HISTORY_TARGET_POINTS_MAX
        ):
            candidates_in_target_range.append(
                candidate
            )

    candidates = (
        candidates_in_target_range
        if candidates_in_target_range
        else all_candidates
    )

    resolution_seconds, _ = min(
        candidates,
        key=lambda item: (
            abs(item[1] - target_points),
            item[0],
        ),
    )

    return resolution_seconds


def choose_daily_resolution_days(
    period_from: datetime,
    period_to: datetime,
    target_points: int,
) -> int:
    total_seconds = (
        period_to - period_from
    ).total_seconds()

    total_days = max(
        1,
        ceil(
            total_seconds / 86400
        ),
    )

    candidates_in_target_range = []
    all_candidates = []

    for resolution_days in (
        DAILY_RESOLUTION_DAYS
    ):
        bucket_count = max(
            1,
            ceil(
                total_days
                / resolution_days
            ),
        )

        candidate = (
            resolution_days,
            bucket_count,
        )

        all_candidates.append(candidate)

        if (
            HISTORY_TARGET_POINTS_MIN
            <= bucket_count
            <= HISTORY_TARGET_POINTS_MAX
        ):
            candidates_in_target_range.append(
                candidate
            )

    candidates = (
        candidates_in_target_range
        if candidates_in_target_range
        else all_candidates
    )

    resolution_days, _ = min(
        candidates,
        key=lambda item: (
            abs(item[1] - target_points),
            item[0],
        ),
    )

    return resolution_days


def _to_float(
    value: object,
) -> float | None:
    if value is None:
        return None

    return float(value)


def _metric_summary(
    row: object,
    metric_name: str,
) -> dict[str, float | None]:
    return {
        "min": _to_float(
            getattr(
                row,
                f"min_{metric_name}",
            )
        ),
        "avg": _to_float(
            getattr(
                row,
                f"avg_{metric_name}",
            )
        ),
        "max": _to_float(
            getattr(
                row,
                f"max_{metric_name}",
            )
        ),
    }


def _weighted_daily_average(
    rows: list[PowerDailySummary],
    metric_name: str,
) -> float | None:
    weighted_sum = 0.0
    total_samples = 0

    for row in rows:
        value = _to_float(
            getattr(
                row,
                f"avg_{metric_name}",
            )
        )

        if (
            value is None
            or row.sample_count <= 0
        ):
            continue

        weighted_sum += (
            value * row.sample_count
        )

        total_samples += row.sample_count

    if total_samples <= 0:
        return None

    return (
        weighted_sum / total_samples
    )


def _daily_metric_summary(
    rows: list[PowerDailySummary],
    metric_name: str,
) -> dict[str, float | None]:
    min_values = []
    max_values = []

    for row in rows:
        min_value = _to_float(
            getattr(
                row,
                f"min_{metric_name}",
            )
        )

        max_value = _to_float(
            getattr(
                row,
                f"max_{metric_name}",
            )
        )

        if min_value is not None:
            min_values.append(
                min_value
            )

        if max_value is not None:
            max_values.append(
                max_value
            )

    return {
        "min": (
            min(min_values)
            if min_values
            else None
        ),
        "avg": (
            _weighted_daily_average(
                rows,
                metric_name,
            )
        ),
        "max": (
            max(max_values)
            if max_values
            else None
        ),
    }


def _sum_daily_consumption(
    rows: list[PowerDailySummary],
) -> float | None:
    values = [
        float(row.consumption_kwh)
        for row in rows
        if row.consumption_kwh
        is not None
    ]

    if not values:
        return None

    return sum(values)


def _first_complete_daily_date(
    period_from: datetime,
) -> date:
    local_from = (
        period_from.astimezone(
            HISTORY_TIMEZONE
        )
    )

    if local_from.time() == time.min:
        return local_from.date()

    return (
        local_from.date()
        + timedelta(days=1)
    )


def _daily_end_exclusive_date(
    period_to: datetime,
) -> date:
    local_to = (
        period_to.astimezone(
            HISTORY_TIMEZONE
        )
    )

    return local_to.date()


def build_detailed_history(
    db: Session,
    channel_id: int,
    period_from: datetime,
    period_to: datetime,
    target_points: int,
) -> dict[str, object]:
    resolution_seconds = (
        choose_history_resolution_seconds(
            period_from=period_from,
            period_to=period_to,
            target_points=target_points,
        )
    )

    period_filter = (
        PowerMeasurement.channel_id
        == channel_id,
        PowerMeasurement.measured_at
        >= period_from,
        PowerMeasurement.measured_at
        < period_to,
    )

    summary_row = db.execute(
        select(
            func.min(
                PowerMeasurement.power_w
            ).label(
                "min_power_w"
            ),
            func.avg(
                PowerMeasurement.power_w
            ).label(
                "avg_power_w"
            ),
            func.max(
                PowerMeasurement.power_w
            ).label(
                "max_power_w"
            ),

            func.min(
                PowerMeasurement.voltage_v
            ).label(
                "min_voltage_v"
            ),
            func.avg(
                PowerMeasurement.voltage_v
            ).label(
                "avg_voltage_v"
            ),
            func.max(
                PowerMeasurement.voltage_v
            ).label(
                "max_voltage_v"
            ),

            func.min(
                PowerMeasurement.current_a
            ).label(
                "min_current_a"
            ),
            func.avg(
                PowerMeasurement.current_a
            ).label(
                "avg_current_a"
            ),
            func.max(
                PowerMeasurement.current_a
            ).label(
                "max_current_a"
            ),

            func.min(
                PowerMeasurement.frequency_hz
            ).label(
                "min_frequency_hz"
            ),
            func.avg(
                PowerMeasurement.frequency_hz
            ).label(
                "avg_frequency_hz"
            ),
            func.max(
                PowerMeasurement.frequency_hz
            ).label(
                "max_frequency_hz"
            ),

            func.min(
                PowerMeasurement.power_factor
            ).label(
                "min_power_factor"
            ),
            func.avg(
                PowerMeasurement.power_factor
            ).label(
                "avg_power_factor"
            ),
            func.max(
                PowerMeasurement.power_factor
            ).label(
                "max_power_factor"
            ),

            func.max(
                PowerMeasurement.energy_kwh
            ).label(
                "max_energy_kwh"
            ),
        ).where(
            *period_filter
        )
    ).one()

    baseline_energy = db.execute(
        select(
            func.max(
                PowerMeasurement.energy_kwh
            )
        ).where(
            PowerMeasurement.channel_id
            == channel_id,
            PowerMeasurement.measured_at
            < period_from,
            PowerMeasurement.energy_kwh.is_not(
                None
            ),
        )
    ).scalar_one_or_none()

    first_period_energy = db.execute(
        select(
            PowerMeasurement.energy_kwh
        )
        .where(
            *period_filter,
            PowerMeasurement.energy_kwh.is_not(
                None
            ),
        )
        .order_by(
            PowerMeasurement.measured_at.asc()
        )
        .limit(1)
    ).scalar_one_or_none()

    energy_reference = _to_float(
        baseline_energy
    )

    if energy_reference is None:
        energy_reference = _to_float(
            first_period_energy
        )

    max_period_energy = _to_float(
        summary_row.max_energy_kwh
    )

    if (
        energy_reference is None
        or max_period_energy is None
    ):
        total_consumption_kwh = None
    else:
        total_consumption_kwh = max(
            0.0,
            max_period_energy
            - energy_reference,
        )

    interval_sql = text(
        "INTERVAL "
        f"'{resolution_seconds} seconds'"
    )

    bucket_start_expression = (
        func.date_bin(
            interval_sql,
            PowerMeasurement.measured_at,
            period_from,
        ).label(
            "bucket_start"
        )
    )

    bucket_rows = db.execute(
        select(
            bucket_start_expression,

            func.count(
                PowerMeasurement.id
            ).label(
                "sample_count"
            ),

            func.avg(
                PowerMeasurement.power_w
            ).label(
                "avg_power_w"
            ),

            func.avg(
                PowerMeasurement.voltage_v
            ).label(
                "avg_voltage_v"
            ),

            func.avg(
                PowerMeasurement.current_a
            ).label(
                "avg_current_a"
            ),

            func.avg(
                PowerMeasurement.frequency_hz
            ).label(
                "avg_frequency_hz"
            ),

            func.avg(
                PowerMeasurement.power_factor
            ).label(
                "avg_power_factor"
            ),

            func.max(
                PowerMeasurement.energy_kwh
            ).label(
                "max_energy_kwh"
            ),

            func.count(
                PowerMeasurement.energy_kwh
            ).label(
                "energy_sample_count"
            ),
        )
        .where(
            *period_filter
        )
        .group_by(
            bucket_start_expression
        )
        .order_by(
            bucket_start_expression
        )
    ).all()

    bucket_duration = timedelta(
        seconds=resolution_seconds
    )

    running_energy_reference = (
        energy_reference
    )

    energy_reference_contiguous = (
        running_energy_reference
        is not None
    )

    previous_bucket_start = None
    points = []

    for row in bucket_rows:
        bucket_start = (
            row.bucket_start
        )

        bucket_end = min(
            bucket_start
            + bucket_duration,
            period_to,
        )

        if previous_bucket_start is None:
            if bucket_start > period_from:
                energy_reference_contiguous = (
                    False
                )

        else:
            expected_bucket_start = (
                previous_bucket_start
                + bucket_duration
            )

            if (
                bucket_start
                > expected_bucket_start
            ):
                energy_reference_contiguous = (
                    False
                )

        energy_sample_count = int(
            row.energy_sample_count
        )

        bucket_max_energy = _to_float(
            row.max_energy_kwh
        )

        bucket_consumption_kwh = None

        if (
            energy_sample_count <= 0
            or bucket_max_energy is None
        ):
            energy_reference_contiguous = (
                False
            )

        elif running_energy_reference is None:
            running_energy_reference = (
                bucket_max_energy
            )

            energy_reference_contiguous = (
                True
            )

        elif not energy_reference_contiguous:
            # Première tranche après un trou :
            # on ne lui attribue pas la
            # consommation du trou précédent.
            bucket_consumption_kwh = None

            if (
                bucket_max_energy
                >= running_energy_reference
            ):
                running_energy_reference = (
                    bucket_max_energy
                )

            energy_reference_contiguous = (
                True
            )

        elif (
            bucket_max_energy
            >= running_energy_reference
        ):
            bucket_consumption_kwh = max(
                0.0,
                bucket_max_energy
                - running_energy_reference,
            )

            running_energy_reference = (
                bucket_max_energy
            )

        else:
            # Diminution anormale du compteur :
            # dernière référence valide conservée.
            bucket_consumption_kwh = 0.0

        points.append(
            {
                "bucket_start": (
                    bucket_start
                ),
                "bucket_end": (
                    bucket_end
                ),
                "sample_count": int(
                    row.sample_count
                ),
                "power_w": _to_float(
                    row.avg_power_w
                ),
                "voltage_v": _to_float(
                    row.avg_voltage_v
                ),
                "current_a": _to_float(
                    row.avg_current_a
                ),
                "frequency_hz": _to_float(
                    row.avg_frequency_hz
                ),
                "power_factor": _to_float(
                    row.avg_power_factor
                ),
                "consumption_kwh": (
                    bucket_consumption_kwh
                ),
            }
        )

        previous_bucket_start = (
            bucket_start
        )

    return {
        "target_points": (
            target_points
        ),
        "returned_points": len(
            points
        ),
        "resolution_seconds": (
            resolution_seconds
        ),
        "source": "detailed",

        "summary": {
            "consumption_kwh": (
                total_consumption_kwh
            ),

            "power_w": _metric_summary(
                summary_row,
                "power_w",
            ),

            "voltage_v": _metric_summary(
                summary_row,
                "voltage_v",
            ),

            "current_a": _metric_summary(
                summary_row,
                "current_a",
            ),

            "frequency_hz": _metric_summary(
                summary_row,
                "frequency_hz",
            ),

            "power_factor": _metric_summary(
                summary_row,
                "power_factor",
            ),
        },

        "points": points,
    }


def build_daily_history(
    db: Session,
    channel_id: int,
    period_from: datetime,
    period_to: datetime,
    target_points: int,
) -> dict[str, object]:
    resolution_days = (
        choose_daily_resolution_days(
            period_from=period_from,
            period_to=period_to,
            target_points=target_points,
        )
    )

    # Les résumés représentent des journées
    # civiles Europe/Paris déjà terminées.
    first_date = (
        _first_complete_daily_date(
            period_from
        )
    )

    end_exclusive_date = (
        _daily_end_exclusive_date(
            period_to
        )
    )

    if (
        first_date
        >= end_exclusive_date
    ):
        daily_rows = []

    else:
        daily_rows = list(
            db.scalars(
                select(
                    PowerDailySummary
                )
                .where(
                    PowerDailySummary.channel_id
                    == channel_id,

                    PowerDailySummary.summary_date
                    >= first_date,

                    PowerDailySummary.summary_date
                    < end_exclusive_date,
                )
                .order_by(
                    PowerDailySummary.summary_date.asc()
                )
            ).all()
        )

    grouped_rows: dict[
        int,
        list[PowerDailySummary],
    ] = {}

    for row in daily_rows:
        day_offset = (
            row.summary_date
            - first_date
        ).days

        bucket_index = (
            day_offset
            // resolution_days
        )

        grouped_rows.setdefault(
            bucket_index,
            [],
        ).append(
            row
        )

    points = []

    for bucket_index in sorted(
        grouped_rows
    ):
        rows = grouped_rows[
            bucket_index
        ]

        bucket_date = (
            first_date
            + timedelta(
                days=(
                    bucket_index
                    * resolution_days
                )
            )
        )

        bucket_end_date = (
            bucket_date
            + timedelta(
                days=resolution_days
            )
        )

        bucket_start = (
            datetime.combine(
                bucket_date,
                time.min,
                tzinfo=HISTORY_TIMEZONE,
            )
        )

        bucket_end = (
            datetime.combine(
                min(
                    bucket_end_date,
                    end_exclusive_date,
                ),
                time.min,
                tzinfo=HISTORY_TIMEZONE,
            )
        )

        points.append(
            {
                "bucket_start": (
                    bucket_start
                ),

                "bucket_end": (
                    bucket_end
                ),

                "sample_count": sum(
                    row.sample_count
                    for row in rows
                ),

                "power_w": (
                    _weighted_daily_average(
                        rows,
                        "power_w",
                    )
                ),

                "voltage_v": (
                    _weighted_daily_average(
                        rows,
                        "voltage_v",
                    )
                ),

                "current_a": (
                    _weighted_daily_average(
                        rows,
                        "current_a",
                    )
                ),

                "frequency_hz": (
                    _weighted_daily_average(
                        rows,
                        "frequency_hz",
                    )
                ),

                "power_factor": (
                    _weighted_daily_average(
                        rows,
                        "power_factor",
                    )
                ),

                "consumption_kwh": (
                    _sum_daily_consumption(
                        rows
                    )
                ),
            }
        )

    return {
        "target_points": (
            target_points
        ),

        "returned_points": len(
            points
        ),

        "resolution_seconds": (
            resolution_days
            * 86400
        ),

        "source": "daily",

        "summary": {
            "consumption_kwh": (
                _sum_daily_consumption(
                    daily_rows
                )
            ),

            "power_w": (
                _daily_metric_summary(
                    daily_rows,
                    "power_w",
                )
            ),

            "voltage_v": (
                _daily_metric_summary(
                    daily_rows,
                    "voltage_v",
                )
            ),

            "current_a": (
                _daily_metric_summary(
                    daily_rows,
                    "current_a",
                )
            ),

            "frequency_hz": (
                _daily_metric_summary(
                    daily_rows,
                    "frequency_hz",
                )
            ),

            "power_factor": (
                _daily_metric_summary(
                    daily_rows,
                    "power_factor",
                )
            ),
        },

        "points": points,
    }