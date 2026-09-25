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
from app.command_security import authorize_device_command
from app.device_transfer_guard import require_active_transfer_access
from app.models import (
    Device,
    DeviceMembership,
    User,
)
from app.mqtt_client import (
    DeviceUnavailableError,
    mqtt_manager,
)
from app.rate_limit import (
    enforce_command_rate_limit,
)
from app.schemas import DeviceCommandResponse


router = APIRouter(
    prefix="/api/v1/devices",
    tags=["commands"],
)


def _send_owner_command(
    device_id: int,
    command: str,
    current_user: User,
    db: Session,
) -> DeviceCommandResponse:
    """Authorize, rate-limit and send one fixed device command."""
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
            status_code=
                status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    device, role = row

    authorization = authorize_device_command(
        role=role,
        device_uid=device.device_uid,
        command=command,
    )

    require_active_transfer_access(
        db, device_id=device.id, user_id=current_user.id, role=role
    )

    enforce_command_rate_limit(
        current_user.id
    )

    if not device.mqtt_device_id:
        raise HTTPException(
            status_code=
                status.HTTP_409_CONFLICT,
            detail=(
                "Device MQTT identity "
                "is not configured"
            ),
        )

    try:
        response = mqtt_manager.send_command(
            mqtt_device_id=
                device.mqtt_device_id,
            authorization=authorization,
            actor_user_id=current_user.id,
            parameters={},
            timeout=5.0,
        )

    except DeviceUnavailableError:
        raise HTTPException(
            status_code=
                status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Device unavailable",
        )

    except TimeoutError:
        raise HTTPException(
            status_code=
                status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Device response timeout",
        )

    except ValueError:
        raise HTTPException(
            status_code=
                status.HTTP_502_BAD_GATEWAY,
            detail="Invalid device response",
        )

    except RuntimeError:
        raise HTTPException(
            status_code=
                status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MQTT unavailable",
        )

    return DeviceCommandResponse(
        device_id=device.id,
        request_id=response["request_id"],
        result=response["result"],
        error_code=response.get(
            "error_code"
        ),
        message=response["message"],
        data=response.get("data"),
    )


@router.post(
    "/{device_id}/commands/ping",
    response_model=DeviceCommandResponse,
)
def ping_device(
    device_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(get_db),
):
    return _send_owner_command(
        device_id=device_id,
        command="ping",
        current_user=current_user,
        db=db,
    )


@router.post(
    "/{device_id}/commands/get_status",
    response_model=DeviceCommandResponse,
)
def get_status_device(
    device_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(get_db),
):
    return _send_owner_command(
        device_id=device_id,
        command="get_status",
        current_user=current_user,
        db=db,
    )
