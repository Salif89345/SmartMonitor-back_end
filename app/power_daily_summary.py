import math

from datetime import (
    date,
    datetime,
    time,
    timedelta,
    timezone,
)
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    PowerDailySummary,
    PowerMeasurement,
)


SMARTMONITOR_TIMEZONE_NAME = "Europe/Paris"

SMARTMONITOR_TIMEZONE = ZoneInfo(
    SMARTMONITOR_TIMEZONE_NAME
)


def local_date_for_timestamp(
    value: datetime,
) -> date:
    if value.tzinfo is None:
        raise ValueError(
            "Timezone-aware datetime required"
        )

    return value.astimezone(
        SMARTMONITOR_TIMEZONE
    ).date()


def _day_utc_bounds(
    summary_date: date,
) -> tuple[datetime, datetime]:
    start_local = datetime.combine(
        summary_date,
        time.min,
        tzinfo=SMARTMONITOR_TIMEZONE,
    )

    end_local = datetime.combine(
        summary_date + timedelta(days=1),
        time.min,
        tzinfo=SMARTMONITOR_TIMEZONE,
    )

    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def _finite_values(
    measurements: list[PowerMeasurement],
    field_name: str,
) -> list[float]:
    values: list[float] = []

    for measurement in measurements:
        value = getattr(
            measurement,
            field_name,
        )

        if value is None:
            continue

        numeric_value = float(value)

        if not math.isfinite(numeric_value):
            continue

        values.append(numeric_value)

    return values


def _min_avg_max(
    measurements: list[PowerMeasurement],
    field_name: str,
) -> tuple[
    float | None,
    float | None,
    float | None,
]:
    values = _finite_values(
        measurements,
        field_name,
    )

    if not values:
        return None, None, None

    return (
        min(values),
        sum(values) / len(values),
        max(values),
    )


def _energy_summary(
    measurements: list[PowerMeasurement],
) -> tuple[
    float | None,
    float | None,
    float | None,
    int,
]:
    values = _finite_values(
        measurements,
        "energy_kwh",
    )

    if not values:
        return None, None, None, 0

    energy_start_kwh = values[0]
    last_valid_kwh = energy_start_kwh

    accepted_value_count = 1
    ignored_decrease_count = 0

    for value in values[1:]:
        if value < last_valid_kwh:
            ignored_decrease_count += 1
            continue

        last_valid_kwh = value
        accepted_value_count += 1

    if accepted_value_count < 2:
        consumption_kwh = None
    else:
        consumption_kwh = (
            last_valid_kwh
            - energy_start_kwh
        )

    return (
        energy_start_kwh,
        last_valid_kwh,
        consumption_kwh,
        ignored_decrease_count,
    )


def _build_summary(
    channel_id: int,
    summary_date: date,
    measurements: list[PowerMeasurement],
) -> tuple[PowerDailySummary, int]:
    if not measurements:
        raise ValueError(
            "At least one measurement is required"
        )

    (
        energy_start_kwh,
        energy_end_kwh,
        consumption_kwh,
        ignored_decrease_count,
    ) = _energy_summary(
        measurements
    )

    (
        min_power_w,
        avg_power_w,
        max_power_w,
    ) = _min_avg_max(
        measurements,
        "power_w",
    )

    (
        min_voltage_v,
        avg_voltage_v,
        max_voltage_v,
    ) = _min_avg_max(
        measurements,
        "voltage_v",
    )

    (
        min_current_a,
        avg_current_a,
        max_current_a,
    ) = _min_avg_max(
        measurements,
        "current_a",
    )

    (
        min_frequency_hz,
        avg_frequency_hz,
        max_frequency_hz,
    ) = _min_avg_max(
        measurements,
        "frequency_hz",
    )

    (
        min_power_factor,
        avg_power_factor,
        max_power_factor,
    ) = _min_avg_max(
        measurements,
        "power_factor",
    )

    summary = PowerDailySummary(
        channel_id=channel_id,
        summary_date=summary_date,
        sample_count=len(measurements),
        first_measured_at=(
            measurements[0].measured_at
        ),
        last_measured_at=(
            measurements[-1].measured_at
        ),
        energy_start_kwh=energy_start_kwh,
        energy_end_kwh=energy_end_kwh,
        consumption_kwh=consumption_kwh,
        min_power_w=min_power_w,
        avg_power_w=avg_power_w,
        max_power_w=max_power_w,
        min_voltage_v=min_voltage_v,
        avg_voltage_v=avg_voltage_v,
        max_voltage_v=max_voltage_v,
        min_current_a=min_current_a,
        avg_current_a=avg_current_a,
        max_current_a=max_current_a,
        min_frequency_hz=min_frequency_hz,
        avg_frequency_hz=avg_frequency_hz,
        max_frequency_hz=max_frequency_hz,
        min_power_factor=min_power_factor,
        avg_power_factor=avg_power_factor,
        max_power_factor=max_power_factor,
    )

    return (
        summary,
        ignored_decrease_count,
    )


def summarize_completed_days(
    db: Session,
    channel_id: int,
    current_local_date: date,
) -> int:
    oldest_measured_at = db.scalar(
        select(
            func.min(
                PowerMeasurement.measured_at
            )
        )
        .where(
            PowerMeasurement.channel_id
            == channel_id
        )
    )

    if oldest_measured_at is None:
        return 0

    candidate_date = local_date_for_timestamp(
        oldest_measured_at
    )

    last_completed_date = (
        current_local_date
        - timedelta(days=1)
    )

    created_count = 0

    while candidate_date <= last_completed_date:
        existing_summary_id = db.scalar(
            select(
                PowerDailySummary.id
            )
            .where(
                PowerDailySummary.channel_id
                == channel_id,
                PowerDailySummary.summary_date
                == candidate_date,
            )
            .limit(1)
        )

        if existing_summary_id is not None:
            candidate_date += timedelta(
                days=1
            )
            continue

        start_utc, end_utc = _day_utc_bounds(
            candidate_date
        )

        measurements = list(
            db.scalars(
                select(
                    PowerMeasurement
                )
                .where(
                    PowerMeasurement.channel_id
                    == channel_id,
                    PowerMeasurement.measured_at
                    >= start_utc,
                    PowerMeasurement.measured_at
                    < end_utc,
                )
                .order_by(
                    PowerMeasurement.measured_at.asc()
                )
            ).all()
        )

        if not measurements:
            candidate_date += timedelta(
                days=1
            )
            continue

        (
            summary,
            ignored_decrease_count,
        ) = _build_summary(
            channel_id=channel_id,
            summary_date=candidate_date,
            measurements=measurements,
        )

        db.add(summary)
        db.commit()
        db.refresh(summary)

        created_count += 1

        print(
            "[DAILY] Power summary stored:",
            "| id:",
            summary.id,
            "| channel_id:",
            channel_id,
            "| date:",
            candidate_date.isoformat(),
            "| samples:",
            summary.sample_count,
            "| consumption_kwh:",
            summary.consumption_kwh,
            "| ignored_energy_decreases:",
            ignored_decrease_count,
        )

        candidate_date += timedelta(
            days=1
        )

    return created_count