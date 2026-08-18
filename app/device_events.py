from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, DeviceEvent


DEVICE_EVENT_TYPES = {
    "device_online",
    "device_offline",
    "command_ack",
    "command_nack",

    "over_current_started",
    "over_current_cleared",

    "over_voltage_started",
    "over_voltage_cleared",

    "under_voltage_started",
    "under_voltage_cleared",

    "over_temperature_started",
    "over_temperature_cleared",

    "sensor_fault",
    "sensor_recovered",
}


def create_device_event(
    db: Session,
    *,
    device_id: int,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> DeviceEvent:
    if event_type not in DEVICE_EVENT_TYPES:
        raise ValueError(
            f"Unsupported device event type: "
            f"{event_type}"
        )

    event = DeviceEvent(
        device_id=device_id,
        event_type=event_type,
        data=data,
    )

    db.add(event)

    return event


def create_device_event_by_mqtt_id(
    db: Session,
    *,
    mqtt_device_id: str,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> DeviceEvent | None:
    device = db.scalar(
        select(Device).where(
            Device.mqtt_device_id
            == mqtt_device_id
        )
    )

    if device is None:
        return None

    event = create_device_event(
        db,
        device_id=device.id,
        event_type=event_type,
        data=data,
    )

    return event
