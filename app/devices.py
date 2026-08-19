from datetime import datetime, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from app.mqtt_client import mqtt_manager
from app.models import (
    Device,
    DeviceChannel,
    DeviceEvent,
    DeviceMembership,
    User,
)
from app.schemas import (
    AddDeviceMemberRequest,
    DeviceAccessPublic,
    DeviceClaimRequest,
    DeviceEventPublic,
    DeviceHistoryResponse,
    DeviceMemberPublic,
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


@router.post(
    "/claim",
    response_model=DeviceAccessPublic,
)
def claim_device(
    payload: DeviceClaimRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    claim_reservation = (
        mqtt_manager.reserve_claim_proof(
            device_uid=payload.device_uid,
            nonce=payload.nonce,
        )
    )

    if claim_reservation is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Device association proof "
                "is invalid or expired"
            ),
        )

    reservation_id, mqtt_device_id = (
        claim_reservation
    )

    try:
        device = db.scalar(
            select(Device)
            .where(
                Device.device_uid
                == payload.device_uid
            )
            .with_for_update()
        )

        if device is None:
            mqtt_conflict = db.scalar(
                select(Device).where(
                    Device.mqtt_device_id
                    == mqtt_device_id
                )
            )

            if mqtt_conflict is not None:
                raise HTTPException(
                    status_code=(
                        status.HTTP_409_CONFLICT
                    ),
                    detail=(
                        "MQTT identity already "
                        "belongs to another device"
                    ),
                )

            device = Device(
                device_uid=(
                    payload.device_uid
                ),
                mqtt_device_id=(
                    mqtt_device_id
                ),
            )

            db.add(
                device
            )

            db.flush()

        else:
            mqtt_conflict = db.scalar(
                select(Device).where(
                    Device.mqtt_device_id
                    == mqtt_device_id,
                    Device.id != device.id,
                )
            )

            if mqtt_conflict is not None:
                raise HTTPException(
                    status_code=(
                        status.HTTP_409_CONFLICT
                    ),
                    detail=(
                        "MQTT identity already "
                        "belongs to another device"
                    ),
                )

            # device_uid reste l'identité matérielle.
            # mqtt_device_id reste une identité de routage.
            device.mqtt_device_id = (
                mqtt_device_id
            )

        owner = db.scalar(
            select(
                DeviceMembership
            ).where(
                DeviceMembership.device_id
                == device.id,

                DeviceMembership.role
                == "owner",
            )
        )

        if (
            owner is not None
            and owner.user_id
            != current_user.id
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_409_CONFLICT
                ),
                detail=(
                    "Device already has an owner"
                ),
            )

        current_membership = db.scalar(
            select(
                DeviceMembership
            ).where(
                DeviceMembership.device_id
                == device.id,

                DeviceMembership.user_id
                == current_user.id,
            )
        )

        if current_membership is None:
            db.add(
                DeviceMembership(
                    user_id=current_user.id,
                    device_id=device.id,
                    role="owner",
                )
            )

        elif current_membership.role != "owner":
            current_membership.role = (
                "owner"
            )

        power_channel = db.scalar(
            select(
                DeviceChannel
            ).where(
                DeviceChannel.device_id
                == device.id,

                DeviceChannel.channel_key
                == "power_1",
            )
        )

        if power_channel is None:
            db.add(
                DeviceChannel(
                    device_id=device.id,
                    channel_key="power_1",
                    name="Power",
                    is_enabled=True,
                )
            )

        db.commit()

        proof_committed = (
            mqtt_manager.commit_claim_proof(
                device_uid=payload.device_uid,
                reservation_id=reservation_id,
            )
        )

        if not proof_committed:
            raise RuntimeError(
                "Claim proof reservation was lost "
                "after database commit"
            )

        db.refresh(
            device
        )

    except HTTPException:
        db.rollback()

        mqtt_manager.release_claim_proof(
            device_uid=payload.device_uid,
            reservation_id=reservation_id,
        )

        raise

    except IntegrityError:
        db.rollback()

        mqtt_manager.release_claim_proof(
            device_uid=payload.device_uid,
            reservation_id=reservation_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "Device association conflict"
            ),
        )

    except Exception:
        db.rollback()

        mqtt_manager.release_claim_proof(
            device_uid=payload.device_uid,
            reservation_id=reservation_id,
        )

        raise

    return build_device_response(
        device,
        "owner",
    )


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


DEVICE_MEMBER_LIMIT = 10


def _require_owner(
    device_id: int,
    current_user: User,
    db: Session,
    *,
    lock_device: bool = False,
) -> Device:
    device_query = select(Device).where(
        Device.id == device_id
    )

    if lock_device:
        device_query = (
            device_query.with_for_update()
        )

    device = db.scalar(
        device_query
    )

    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    membership = db.scalar(
        select(DeviceMembership).where(
            DeviceMembership.device_id
            == device_id,
            DeviceMembership.user_id
            == current_user.id,
        )
    )

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    if membership.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    return device


@router.get(
    "/{device_id}/members",
    response_model=list[DeviceMemberPublic],
)
def list_device_members(
    device_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    _require_owner(
        device_id,
        current_user,
        db,
    )

    rows = db.execute(
        select(
            User.id,
            User.email,
        )
        .join(
            DeviceMembership,
            DeviceMembership.user_id
            == User.id,
        )
        .where(
            DeviceMembership.device_id
            == device_id,
            DeviceMembership.role
            == "member",
        )
        .order_by(
            User.id
        )
    ).all()

    return [
        DeviceMemberPublic(
            user_id=user_id,
            email=email,
            role="member",
        )
        for user_id, email in rows
    ]


@router.post(
    "/{device_id}/members",
    response_model=DeviceMemberPublic,
    status_code=status.HTTP_201_CREATED,
)
def add_device_member(
    device_id: int,
    payload: AddDeviceMemberRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    _require_owner(
        device_id,
        current_user,
        db,
        lock_device=True,
    )

    normalized_email = payload.email.lower()

    member_user = db.scalar(
        select(User).where(
            User.email == normalized_email
        )
    )

    if (
        member_user is None
        or not member_user.is_active
        or not member_user.email_verified
    ):
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not available",
        )

    existing_membership = db.scalar(
        select(DeviceMembership).where(
            DeviceMembership.device_id
            == device_id,
            DeviceMembership.user_id
            == member_user.id,
        )
    )

    if existing_membership is not None:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has access",
        )

    member_count = db.scalar(
        select(func.count())
        .select_from(DeviceMembership)
        .where(
            DeviceMembership.device_id
            == device_id,
            DeviceMembership.role
            == "member",
        )
    )

    if member_count >= DEVICE_MEMBER_LIMIT:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Member limit reached",
        )

    membership = DeviceMembership(
        user_id=member_user.id,
        device_id=device_id,
        role="member",
    )

    db.add(
        membership
    )

    db.commit()

    return DeviceMemberPublic(
        user_id=member_user.id,
        email=member_user.email,
        role="member",
    )


@router.delete(
    "/{device_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_device_member(
    device_id: int,
    user_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    _require_owner(
        device_id,
        current_user,
        db,
        lock_device=True,
    )

    membership = db.scalar(
        select(DeviceMembership).where(
            DeviceMembership.device_id
            == device_id,
            DeviceMembership.user_id
            == user_id,
            DeviceMembership.role
            == "member",
        )
    )

    if membership is None:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    db.delete(
        membership
    )

    db.commit()

    return None


@router.get(
    "/{device_id}/events",
    response_model=list[DeviceEventPublic],
)
def list_device_events(
    device_id: int,

    limit: Annotated[
        int,
        Query(
            ge=1,
            le=200,
        ),
    ] = 100,

    current_user: User = Depends(
        get_current_user
    ),

    db: Session = Depends(
        get_db
    ),
):
    membership = db.scalar(
        select(DeviceMembership).where(
            DeviceMembership.device_id
            == device_id,

            DeviceMembership.user_id
            == current_user.id,
        )
    )

    if membership is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail="Device not found",
        )

    events = db.scalars(
        select(DeviceEvent)
        .where(
            DeviceEvent.device_id
            == device_id
        )
        .order_by(
            DeviceEvent.created_at.desc(),
            DeviceEvent.id.desc(),
        )
        .limit(limit)
    ).all()

    return list(events)


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
