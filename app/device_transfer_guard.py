"""Fail-closed access boundaries while a physical transfer is pending.

Disabled in the current deployment because the SM-015 migration has not been
applied. When enabled, callers must use these guards on every user-facing
device action and history route before transfer staging is exposed.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeviceTransfer
from app.settings import SM015_TRANSFER_STAGING_ENABLED


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def pending_transfer(db: Session, device_id: int) -> DeviceTransfer | None:
    if not SM015_TRANSFER_STAGING_ENABLED:
        return None
    return db.scalar(
        select(DeviceTransfer).where(
            DeviceTransfer.device_id == device_id,
            DeviceTransfer.state == "pending",
        )
    )


def latest_completed_transfer(db: Session, device_id: int) -> DeviceTransfer | None:
    if not SM015_TRANSFER_STAGING_ENABLED:
        return None
    return db.scalar(
        select(DeviceTransfer)
        .where(
            DeviceTransfer.device_id == device_id,
            DeviceTransfer.state == "completed",
        )
        .order_by(DeviceTransfer.effective_at.desc(), DeviceTransfer.id.desc())
        .limit(1)
    )


def active_transfer_access_allowed(
    db: Session, *, device_id: int, user_id: int, role: str
) -> bool:
    if not SM015_TRANSFER_STAGING_ENABLED:
        return True
    if pending_transfer(db, device_id) is not None:
        return False
    latest = latest_completed_transfer(db, device_id)
    return latest is None or (
        latest.effective_at is not None
        and role == "owner"
        and latest.next_owner_user_id == user_id
    )


def require_active_transfer_access(
    db: Session, *, device_id: int, user_id: int, role: str
) -> None:
    if pending_transfer(db, device_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device transfer pending",
        )
    latest = latest_completed_transfer(db, device_id)
    if latest is not None and (
        latest.effective_at is None
        or role != "owner"
        or latest.next_owner_user_id != user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Device access from previous ownership period denied",
        )


def bounded_history_period(
    db: Session,
    *,
    device_id: int,
    user_id: int,
    role: str,
    period_from: datetime,
    period_to: datetime,
) -> tuple[datetime, datetime]:
    """Exclude pending-period data and all data before the last new owner.

    This bound is for current members only. An old owner's archive requires a
    separate endpoint, because their active membership is removed on transfer.
    """
    if not SM015_TRANSFER_STAGING_ENABLED:
        return period_from, period_to
    latest_completed = latest_completed_transfer(db, device_id)
    start = period_from
    if latest_completed is not None:
        # Existing memberships from a prior ownership epoch must never grant
        # access to the new owner's measurements. Member re-invitation needs
        # an explicit epoch-aware model; until then only the new owner reads.
        if role != "owner" or latest_completed.next_owner_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="History outside ownership period",
            )
        if latest_completed.effective_at is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Device transfer history unavailable",
            )
        start = max(start, _aware(latest_completed.effective_at))
    pending = pending_transfer(db, device_id)
    end = period_to
    if pending is not None:
        end = min(end, _aware(pending.requested_at))
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="History outside ownership period",
        )
    return start, end
