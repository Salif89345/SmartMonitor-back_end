import math
import re

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.device_events import create_device_event
from app.models import AlarmOccurrence, Device


ACTIVE_ALARM_STATES = {"active", "pending_clear"}
KNOWN_ALARM_STATES = {
    "normal",
    "pending_active",
    "active",
    "pending_clear",
}
KNOWN_SEVERITIES = {"info", "warning", "critical"}
ALARM_KEY_PATTERN = re.compile(r"^[a-z0-9_-]{1,63}$")


def _finite_float(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _active_occurrence(
    db: Session,
    *,
    device_id: int,
    alarm_key: str,
) -> AlarmOccurrence | None:
    return db.scalar(
        select(AlarmOccurrence)
        .where(
            AlarmOccurrence.device_id == device_id,
            AlarmOccurrence.alarm_key == alarm_key,
            AlarmOccurrence.status == "active",
        )
        .order_by(
            AlarmOccurrence.activated_at.desc(),
            AlarmOccurrence.id.desc(),
        )
        .limit(1)
    )


def ingest_alarm_snapshot(
    db: Session,
    *,
    mqtt_device_id: str,
    alarms: Any,
    boot_count: Any,
    occurred_at: datetime | None,
) -> int:
    """Persist alarm transitions once, despite periodic MQTT state repeats."""
    if (
        not isinstance(alarms, dict)
        or not isinstance(boot_count, int)
        or isinstance(boot_count, bool)
        or boot_count < 0
    ):
        return 0

    raw_items = alarms.get("items")
    if not isinstance(raw_items, list):
        return 0

    device = db.scalar(
        select(Device).where(Device.mqtt_device_id == mqtt_device_id)
    )
    if device is None:
        return 0

    event_time = occurred_at or datetime.now(timezone.utc)
    changes = 0
    seen_alarm_keys: set[str] = set()
    snapshot_complete = alarms.get("ready") is True

    for raw in raw_items:
        if not isinstance(raw, dict):
            snapshot_complete = False
            continue

        alarm_key = raw.get("id")
        state = raw.get("state")
        severity = raw.get("severity")
        transition_count = raw.get("transition_count")

        if (
            not isinstance(alarm_key, str)
            or ALARM_KEY_PATTERN.fullmatch(alarm_key) is None
            or state not in KNOWN_ALARM_STATES
            or severity not in KNOWN_SEVERITIES
            or not isinstance(transition_count, int)
            or isinstance(transition_count, bool)
            or transition_count < 0
        ):
            snapshot_complete = False
            continue

        seen_alarm_keys.add(alarm_key)

        active = _active_occurrence(
            db,
            device_id=device.id,
            alarm_key=alarm_key,
        )

        if state in ACTIVE_ALARM_STATES:
            if transition_count == 0:
                continue

            if (
                active is not None
                and active.source_boot_count == boot_count
                and active.activation_transition_count == transition_count
            ):
                active.value = _finite_float(raw.get("value"))
                active.threshold = _finite_float(raw.get("threshold"))
                continue

            if active is not None:
                active.status = "cleared"
                active.cleared_at = event_time
                active.clear_transition_count = transition_count
                create_device_event(
                    db,
                    device_id=device.id,
                    event_type="alarm_cleared",
                    data={
                        "alarm_occurrence_id": active.id,
                        "alarm_key": alarm_key,
                        "reason": "superseded",
                    },
                )

            occurrence = AlarmOccurrence(
                device_id=device.id,
                alarm_key=alarm_key,
                severity=severity,
                status="active",
                source_boot_count=boot_count,
                activation_transition_count=transition_count,
                value=_finite_float(raw.get("value")),
                threshold=_finite_float(raw.get("threshold")),
                activated_at=event_time,
            )
            db.add(occurrence)
            db.flush()
            create_device_event(
                db,
                device_id=device.id,
                event_type="alarm_activated",
                data={
                    "alarm_occurrence_id": occurrence.id,
                    "alarm_key": alarm_key,
                    "severity": severity,
                },
            )
            changes += 1
            continue

        if active is None:
            continue

        active.status = "cleared"
        active.cleared_at = event_time
        active.clear_transition_count = transition_count
        create_device_event(
            db,
            device_id=device.id,
            event_type="alarm_cleared",
            data={
                "alarm_occurrence_id": active.id,
                "alarm_key": alarm_key,
                "reason": "threshold_normal",
            },
        )
        changes += 1

    # A disabled or reset rule disappears from the firmware snapshot. Close any
    # occurrence still open for such a rule, but only from a complete snapshot
    # so a malformed payload can never clear a real alarm accidentally.
    if snapshot_complete:
        active_occurrences = db.scalars(
            select(AlarmOccurrence).where(
                AlarmOccurrence.device_id == device.id,
                AlarmOccurrence.status == "active",
            )
        ).all()
        for active in active_occurrences:
            if active.alarm_key in seen_alarm_keys:
                continue

            active.status = "cleared"
            active.cleared_at = event_time
            active.clear_transition_count = None
            create_device_event(
                db,
                device_id=device.id,
                event_type="alarm_cleared",
                data={
                    "alarm_occurrence_id": active.id,
                    "alarm_key": active.alarm_key,
                    "reason": "rule_disabled_or_missing",
                },
            )
            changes += 1

    return changes


def acknowledge_alarm(
    db: Session,
    *,
    occurrence: AlarmOccurrence,
    user_id: int,
) -> AlarmOccurrence:
    if occurrence.acknowledged_at is not None:
        return occurrence

    occurrence.acknowledged_at = datetime.now(timezone.utc)
    occurrence.acknowledged_by_user_id = user_id
    create_device_event(
        db,
        device_id=occurrence.device_id,
        event_type="alarm_acknowledged",
        data={
            "alarm_occurrence_id": occurrence.id,
            "alarm_key": occurrence.alarm_key,
        },
    )
    return occurrence
