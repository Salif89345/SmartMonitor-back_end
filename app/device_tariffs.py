"""User-owned electricity price and server-side daily cost estimate."""

import math

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.device_authorization import DeviceAction, require_device_action
from app.device_live_state import live_state_store
from app.device_transfer_guard import require_active_transfer_access
from app.models import Device, DeviceMembership, DeviceTariff, User


router = APIRouter(prefix="/api/v1/devices", tags=["tariff"])


class TariffUpdate(BaseModel):
    eur_per_kwh: float | None = Field(ge=0, le=10, allow_inf_nan=False)


class TariffPublic(BaseModel):
    eur_per_kwh: float | None
    local_date: int | None
    daily_energy_kwh: float | None
    estimated_cost_eur: float | None


def _accessible_device(db: Session, device_id: int, user_id: int, action: DeviceAction) -> Device:
    row = db.execute(
        select(Device, DeviceMembership.role).join(
            DeviceMembership, DeviceMembership.device_id == Device.id
        ).where(Device.id == device_id, DeviceMembership.user_id == user_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device, role = row
    require_device_action(role, action)
    require_active_transfer_access(db, device_id=device.id, user_id=user_id, role=role)
    return device


def _public_tariff(db: Session, device: Device, user_id: int) -> TariffPublic:
    preference = db.get(DeviceTariff, (user_id, device.id))
    tariff = preference.eur_per_kwh if preference is not None else None
    snapshot = live_state_store.get(device.mqtt_device_id) if device.mqtt_device_id else None
    daily = snapshot.get("daily_energy") if isinstance(snapshot, dict) else None
    energy = daily.get("energy_kwh") if isinstance(daily, dict) else None
    local_date = daily.get("local_date") if isinstance(daily, dict) else None
    if not isinstance(energy, (int, float)) or not math.isfinite(energy) or not 0 <= energy <= 1000000000:
        energy = None
        local_date = None
    return TariffPublic(
        eur_per_kwh=tariff,
        local_date=local_date,
        daily_energy_kwh=energy,
        estimated_cost_eur=energy * tariff if energy is not None and tariff is not None else None,
    )


@router.get("/{device_id}/tariff", response_model=TariffPublic)
def get_device_tariff(
    device_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _accessible_device(db, device_id, current_user.id, DeviceAction.READ_DEVICE)
    return _public_tariff(db, device, current_user.id)


@router.post("/{device_id}/tariff", response_model=TariffPublic)
def set_device_tariff(
    device_id: int,
    request: TariffUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _accessible_device(db, device_id, current_user.id, DeviceAction.READ_DEVICE)
    preference = db.get(DeviceTariff, (current_user.id, device.id))
    if request.eur_per_kwh is None:
        if preference is not None:
            db.delete(preference)
    elif preference is None:
        db.add(DeviceTariff(
            user_id=current_user.id,
            device_id=device.id,
            eur_per_kwh=request.eur_per_kwh,
        ))
    else:
        preference.eur_per_kwh = request.eur_per_kwh
    db.commit()
    return _public_tariff(db, device, current_user.id)
