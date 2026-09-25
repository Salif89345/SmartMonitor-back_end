"""Assign one accepted measurement to one immutable owner/transfer epoch.

Call inside the same transaction as the measurement insert. The device row is
locked so a future transfer finalizer can serialize ownership cutover with
ingestion. No attribution is issued during a pending transfer or inconsistent
membership state.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Device,
    DeviceChannel,
    DeviceMembership,
    DeviceTransfer,
    PowerMeasurement,
    PowerMeasurementAttribution,
)


class MeasurementOwnerUnavailable(RuntimeError):
    """Reject the measurement rather than assign it to the wrong account."""


@dataclass(frozen=True)
class OwnerEpoch:
    device_id: int
    owner_user_id: int
    epoch_transfer_id: int | None
    effective_at: datetime | None


def resolve_owner_epoch(db: Session, *, channel_id: int) -> OwnerEpoch:
    device_id = db.scalar(
        select(DeviceChannel.device_id).where(DeviceChannel.id == channel_id)
    )
    if device_id is None:
        raise MeasurementOwnerUnavailable("measurement channel missing")
    device = db.scalar(select(Device).where(Device.id == device_id).with_for_update())
    if device is None or not device.is_active:
        raise MeasurementOwnerUnavailable("device unavailable")
    pending = db.scalar(
        select(DeviceTransfer.id).where(
            DeviceTransfer.device_id == device_id,
            DeviceTransfer.state == "pending",
        )
    )
    if pending is not None:
        raise MeasurementOwnerUnavailable("ownership transfer pending")
    owners = list(
        db.scalars(
            select(DeviceMembership).where(
                DeviceMembership.device_id == device_id,
                DeviceMembership.role == "owner",
            )
        ).all()
    )
    if len(owners) != 1:
        raise MeasurementOwnerUnavailable("one active owner required")
    latest = db.scalar(
        select(DeviceTransfer)
        .where(
            DeviceTransfer.device_id == device_id,
            DeviceTransfer.state == "completed",
        )
        .order_by(DeviceTransfer.effective_at.desc(), DeviceTransfer.id.desc())
        .limit(1)
    )
    if latest is not None and (
        latest.effective_at is None or latest.next_owner_user_id != owners[0].user_id
    ):
        raise MeasurementOwnerUnavailable("ownership epoch inconsistent")
    return OwnerEpoch(
        device_id=device_id,
        owner_user_id=owners[0].user_id,
        epoch_transfer_id=latest.id if latest is not None else None,
        effective_at=latest.effective_at if latest is not None else None,
    )


def require_measurement_in_epoch(epoch: OwnerEpoch, *, measured_at: datetime) -> None:
    """Do not relabel an old, delayed MQTT state as the new owner's data."""
    if measured_at.tzinfo is None or measured_at.utcoffset() is None:
        raise MeasurementOwnerUnavailable("measurement timestamp must be aware")
    if epoch.effective_at is None:
        return
    effective_at = epoch.effective_at
    if effective_at.tzinfo is None:
        effective_at = effective_at.replace(tzinfo=timezone.utc)
    if measured_at < effective_at:
        raise MeasurementOwnerUnavailable("measurement predates ownership epoch")


def attach_measurement_owner(
    db: Session,
    *,
    measurement: PowerMeasurement,
    epoch: OwnerEpoch,
    now: datetime | None = None,
) -> PowerMeasurementAttribution:
    if measurement.id is None:
        raise ValueError("measurement must be flushed before attribution")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    attribution = PowerMeasurementAttribution(
        measurement_id=measurement.id,
        device_id=epoch.device_id,
        owner_user_id=epoch.owner_user_id,
        epoch_transfer_id=epoch.epoch_transfer_id,
        attributed_at=current.astimezone(timezone.utc),
    )
    db.add(attribution)
    return attribution
