"""Authenticated, durable SM-015-SEC in-app transfer notice inbox.

The router is not mounted by default. Its table migration and the surrounding
transfer safety work must be deployed before enabling it.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import DeviceTransferNotice, User


router = APIRouter(prefix="/api/v1/transfer-notices", tags=["transfer-notices"])


class TransferNoticePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phase: str
    body: str
    created_at: datetime
    read_at: datetime | None


def list_notices_for_user(
    db: Session, *, user_id: int, limit: int = 100
) -> list[DeviceTransferNotice]:
    if not 1 <= limit <= 100:
        raise ValueError("notice limit must be 1-100")
    return list(
        db.scalars(
            select(DeviceTransferNotice)
            .where(DeviceTransferNotice.user_id == user_id)
            .order_by(
                DeviceTransferNotice.created_at.desc(),
                DeviceTransferNotice.id.desc(),
            )
            .limit(limit)
        ).all()
    )


def mark_notice_read(
    db: Session, *, user_id: int, notice_id: int, now: datetime | None = None
) -> DeviceTransferNotice | None:
    """Mark only this user's notice; unknown and foreign IDs look identical."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    notice = db.scalar(
        select(DeviceTransferNotice)
        .where(
            DeviceTransferNotice.id == notice_id,
            DeviceTransferNotice.user_id == user_id,
        )
        .with_for_update()
    )
    if notice is None:
        return None
    if notice.read_at is None:
        notice.read_at = current.astimezone(timezone.utc)
        db.commit()
    return notice


@router.get("", response_model=list[TransferNoticePublic])
def list_my_transfer_notices(
    limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_notices_for_user(db, user_id=current_user.id, limit=limit)


@router.post("/{notice_id}/read", response_model=TransferNoticePublic)
def read_my_transfer_notice(
    notice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notice = mark_notice_read(db, user_id=current_user.id, notice_id=notice_id)
    if notice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notice not found",
        )
    return notice
