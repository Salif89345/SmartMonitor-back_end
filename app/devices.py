from datetime import datetime, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
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
    build_daily_history,
    build_detailed_history,
)
from app.models import (
    Device,
    DeviceChannel,
    DeviceMembership,
    User,
)
from app.schemas import (
    DeviceAccessPublic,
    DeviceHistoryResponse,
)


router = APIRouter(
    prefix="/api/v1/devices",
    tags=["devices"],
)


def build_device_response(
    device: Device,
    role: str,
) -> DeviceAccessPublic:
    return DeviceAccessPublic(
        id=device.id,
        device_uid=device.device_uid,
        name=device.name,
        is_active=device.is_active,
        created_at=device.created_at,
        role=role,
    )


@router.get(
    "",
    response_model=list[
        DeviceAccessPublic
    ],
)
def list_my_devices(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    rows = db.execute(
        select(
            Device,
            DeviceMembership.role,
        )
        .join(
            DeviceMembership,
            DeviceMembership.device_id
            == Device.id,
        )
        .where(
            DeviceMembership.user_id
            == current_user.id
        )
        .order_by(
            Device.id
        )
    ).all()

    return [
        build_device_response(
            device,
            role,
        )
        for device, role in rows
    ]


@router.get(
    "/{device_id}",
    response_model=DeviceAccessPublic,
)
def get_my_device(
    device_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    row = db.execute(
        select(
            Device,
            DeviceMembership.role,
        )
        .join(
            DeviceMembership,
            DeviceMembership.device_id
            == Device.id,
        )
        .where(
            Device.id == device_id,
            DeviceMembership.user_id
            == current_user.id,
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail="Device not found",
        )

    device, role = row

    return build_device_response(
        device,
        role,
    )


@router.get(
    (
        "/{device_id}/channels/"
        "{channel_id}/history"
    ),
    response_model=DeviceHistoryResponse,
)
def get_channel_history(
    device_id: int,
    channel_id: int,

    from_: Annotated[
        datetime,
        Query(alias="from"),
    ],

    to: Annotated[
        datetime,
        Query(),
    ],

    target_points: Annotated[
        int,
        Query(
            ge=HISTORY_TARGET_POINTS_MIN,
            le=HISTORY_TARGET_POINTS_MAX,
        ),
    ] = HISTORY_TARGET_POINTS_DEFAULT,

    current_user: User = Depends(
        get_current_user
    ),

    db: Session = Depends(
        get_db
    ),
):
    if (
        from_.utcoffset() is None
        or to.utcoffset() is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "HISTORY_TIMEZONE_REQUIRED"
            ),
        )

    if from_ >= to:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "HISTORY_INVALID_RANGE"
            ),
        )

    history_duration = (
        to - from_
    )

    if (
        history_duration
        > timedelta(
            days=HISTORY_MAX_DAYS
        )
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "HISTORY_RANGE_TOO_LARGE"
            ),
        )

    channel = db.execute(
        select(
            DeviceChannel
        )
        .join(
            DeviceMembership,
            DeviceMembership.device_id
            == DeviceChannel.device_id,
        )
        .where(
            DeviceChannel.id
            == channel_id,

            DeviceChannel.device_id
            == device_id,

            DeviceMembership.user_id
            == current_user.id,
        )
    ).scalar_one_or_none()

    if channel is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "HISTORY_NOT_FOUND"
            ),
        )

    if (
        history_duration
        <= timedelta(
            days=(
                DETAILED_HISTORY_MAX_DAYS
            )
        )
    ):
        history = (
            build_detailed_history(
                db=db,
                channel_id=channel.id,
                period_from=from_,
                period_to=to,
                target_points=target_points,
            )
        )

    else:
        history = (
            build_daily_history(
                db=db,
                channel_id=channel.id,
                period_from=from_,
                period_to=to,
                target_points=target_points,
            )
        )

    return DeviceHistoryResponse(
        device_id=device_id,
        channel_id=channel.id,

        period={
            "from": from_,
            "to": to,
        },

        **history,
    )