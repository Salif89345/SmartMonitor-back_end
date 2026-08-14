from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    Device,
    DeviceMembership,
    User,
)
from app.schemas import DeviceAccessPublic


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
    response_model=list[DeviceAccessPublic],
)
def list_my_devices(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(get_db),
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
        .order_by(Device.id)
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
    db: Session = Depends(get_db),
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    device, role = row

    return build_device_response(
        device,
        role,
    )