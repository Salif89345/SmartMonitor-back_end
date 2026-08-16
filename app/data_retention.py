from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import (
    PowerDailySummary,
    PowerMeasurement,
)


POWER_MEASUREMENT_RETENTION_DAYS = 90
POWER_DAILY_SUMMARY_RETENTION_DAYS = 365


def cleanup_channel_history(
    db: Session,
    channel_id: int,
    reference_time: datetime,
    current_local_date: date,
) -> dict:
    if reference_time.tzinfo is None:
        raise ValueError(
            "Timezone-aware reference_time required"
        )

    reference_time_utc = (
        reference_time.astimezone(timezone.utc)
    )

    measurement_cutoff = (
        reference_time_utc
        - timedelta(
            days=POWER_MEASUREMENT_RETENTION_DAYS
        )
    )

    summary_cutoff_date = (
        current_local_date
        - timedelta(
            days=POWER_DAILY_SUMMARY_RETENTION_DAYS
        )
    )

    measurement_result = db.execute(
        delete(PowerMeasurement)
        .where(
            PowerMeasurement.channel_id
            == channel_id,
            PowerMeasurement.measured_at
            < measurement_cutoff,
        )
    )

    summary_result = db.execute(
        delete(PowerDailySummary)
        .where(
            PowerDailySummary.channel_id
            == channel_id,
            PowerDailySummary.summary_date
            < summary_cutoff_date,
        )
    )

    db.commit()

    deleted_measurements = max(
        measurement_result.rowcount or 0,
        0,
    )

    deleted_summaries = max(
        summary_result.rowcount or 0,
        0,
    )

    return {
        "deleted_measurements":
            deleted_measurements,
        "deleted_summaries":
            deleted_summaries,
        "measurement_cutoff":
            measurement_cutoff,
        "summary_cutoff_date":
            summary_cutoff_date,
    }