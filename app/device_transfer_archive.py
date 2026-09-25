"""Read-only measurements from a former owner's completed ownership period.

Mounted only with SM015_TRANSFER_STAGING_ENABLED after its database migration.
Each response is restricted to one unambiguous former-ownership window.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.history_service import (
    DETAILED_HISTORY_MAX_DAYS,
    HISTORY_MAX_DAYS,
    HISTORY_TARGET_POINTS_DEFAULT,
    HISTORY_TARGET_POINTS_MAX,
    HISTORY_TARGET_POINTS_MIN,
    build_detailed_history,
)
from app.models import (
    Device,
    DeviceChannel,
    DeviceTransfer,
    PowerMeasurement,
    PowerMeasurementAttribution,
    User,
)
from app.schemas import DeviceHistoryResponse


router = APIRouter(prefix="/api/v1/devices", tags=["transfer-archive"])
archive_index_router = APIRouter(
    prefix="/api/v1/transfer-archives", tags=["transfer-archive"]
)


class FormerOwnerArchivePublic(BaseModel):
    archive_id: int
    device_id: int
    device_uid: str
    period_from: datetime
    period_to: datetime
    channel_ids: list[int]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _completed_ownership_epochs(
    db: Session, device_id: int
) -> tuple[Device, list[tuple[DeviceTransfer, datetime, datetime, int | None]]]:
    """Validate the whole transfer chain before showing any archived data."""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Archive not found")
    transfers = list(
        db.scalars(
            select(DeviceTransfer)
            .where(
                DeviceTransfer.device_id == device_id,
                DeviceTransfer.state == "completed",
            )
            .order_by(DeviceTransfer.effective_at, DeviceTransfer.id)
        ).all()
    )
    if not transfers:
        raise HTTPException(status_code=404, detail="Archive not found")
    expected_owner = transfers[0].previous_owner_user_id
    window_start = _aware(device.created_at)
    epochs: list[tuple[DeviceTransfer, datetime, datetime, int | None]] = []
    epoch_transfer_id: int | None = None
    for transfer in transfers:
        effective = transfer.effective_at
        if (
            effective is None
            or transfer.previous_owner_user_id != expected_owner
            or _aware(transfer.requested_at) <= window_start
            or _aware(transfer.not_before) <= _aware(transfer.requested_at)
            or _aware(effective) < _aware(transfer.not_before)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ownership archive inconsistent",
            )
        # Data received during the one-minute freeze belongs to neither user.
        window_end = _aware(transfer.requested_at)
        epochs.append((transfer, window_start, window_end, epoch_transfer_id))
        expected_owner = transfer.next_owner_user_id
        window_start = _aware(effective)
        epoch_transfer_id = transfer.id
    return device, epochs


def former_owner_window(
    db: Session,
    *,
    device_id: int,
    user_id: int,
    period_from: datetime,
    period_to: datetime,
) -> tuple[datetime, datetime, int | None]:
    """Return the overlap with exactly one past ownership epoch or deny it."""
    _, epochs = _completed_ownership_epochs(db, device_id)
    overlaps: list[tuple[datetime, datetime, int | None]] = []
    for transfer, window_start, window_end, epoch_transfer_id in epochs:
        if user_id == transfer.previous_owner_user_id:
            start = max(period_from, window_start)
            end = min(period_to, window_end)
            if start < end:
                overlaps.append((start, end, epoch_transfer_id))
    if len(overlaps) != 1:
        # No access, or a range spanning multiple separate ownership epochs.
        raise HTTPException(status_code=404, detail="Archive not found")
    return overlaps[0]


def list_former_owner_archives(
    db: Session, *, user_id: int
) -> list[FormerOwnerArchivePublic]:
    """List only this account's former periods, outside the active device list."""
    device_ids = list(
        db.scalars(
            select(DeviceTransfer.device_id)
            .where(
                DeviceTransfer.previous_owner_user_id == user_id,
                DeviceTransfer.state == "completed",
            )
            .distinct()
        ).all()
    )
    archives: list[FormerOwnerArchivePublic] = []
    for device_id in device_ids:
        device, epochs = _completed_ownership_epochs(db, device_id)
        for transfer, start, end, epoch_transfer_id in epochs:
            if transfer.previous_owner_user_id == user_id:
                channel_statement = (
                    select(DeviceChannel.id)
                    .join(PowerMeasurement, PowerMeasurement.channel_id == DeviceChannel.id)
                    .join(
                        PowerMeasurementAttribution,
                        PowerMeasurementAttribution.measurement_id == PowerMeasurement.id,
                    )
                    .where(
                        DeviceChannel.device_id == device_id,
                        PowerMeasurementAttribution.owner_user_id == user_id,
                        (
                            PowerMeasurementAttribution.epoch_transfer_id.is_(None)
                            if epoch_transfer_id is None
                            else PowerMeasurementAttribution.epoch_transfer_id
                            == epoch_transfer_id
                        ),
                    )
                    .distinct()
                    .order_by(DeviceChannel.id)
                )
                channel_ids = list(db.scalars(channel_statement).all())
                archives.append(
                    FormerOwnerArchivePublic(
                        archive_id=transfer.id,
                        device_id=device_id,
                        device_uid=device.device_uid,
                        period_from=start,
                        period_to=end,
                        channel_ids=channel_ids,
                    )
                )
    archives.sort(key=lambda archive: archive.period_to, reverse=True)
    return archives


@archive_index_router.get("", response_model=list[FormerOwnerArchivePublic])
def get_my_former_owner_archives(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_former_owner_archives(db, user_id=current_user.id)


@router.get(
    "/{device_id}/channels/{channel_id}/history-archive",
    response_model=DeviceHistoryResponse,
)
def get_former_owner_history(
    device_id: int,
    channel_id: int,
    from_: Annotated[datetime, Query(alias="from")],
    to: Annotated[datetime, Query()],
    target_points: Annotated[
        int,
        Query(ge=HISTORY_TARGET_POINTS_MIN, le=HISTORY_TARGET_POINTS_MAX),
    ] = HISTORY_TARGET_POINTS_DEFAULT,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if from_.utcoffset() is None or to.utcoffset() is None:
        raise HTTPException(status_code=422, detail="HISTORY_TIMEZONE_REQUIRED")
    if from_ >= to:
        raise HTTPException(status_code=422, detail="HISTORY_INVALID_RANGE")
    if to - from_ > timedelta(days=HISTORY_MAX_DAYS):
        raise HTTPException(status_code=422, detail="HISTORY_RANGE_TOO_LARGE")
    channel_exists = db.scalar(
        select(DeviceChannel.id).where(
            DeviceChannel.id == channel_id,
            DeviceChannel.device_id == device_id,
        )
    )
    if channel_exists is None:
        raise HTTPException(status_code=404, detail="Archive not found")
    visible_from, visible_to, epoch_transfer_id = former_owner_window(
        db,
        device_id=device_id,
        user_id=current_user.id,
        period_from=from_,
        period_to=to,
    )
    if visible_to - visible_from > timedelta(days=DETAILED_HISTORY_MAX_DAYS):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Owner-specific daily history is not yet available",
        )
    history = build_detailed_history(
        db=db,
        channel_id=channel_id,
        period_from=visible_from,
        period_to=visible_to,
        target_points=target_points,
        owner_user_id=current_user.id,
        epoch_transfer_id=epoch_transfer_id,
    )
    return DeviceHistoryResponse(
        device_id=device_id,
        channel_id=channel_id,
        period={"from": visible_from, "to": visible_to},
        **history,
    )
