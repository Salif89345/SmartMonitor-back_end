"""Owner-managed alarm rules, copied to the ESP for offline evaluation."""

import math
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.commands import _send_owner_command
from app.database import get_db
from app.device_authorization import DeviceAction
from app.device_tariffs import _accessible_device
from app.models import DeviceAlarmConfiguration, User
from app.mqtt_client import mqtt_manager


router = APIRouter(prefix="/api/v1/devices", tags=["alarm configuration"])


class AlarmRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_-]{1,23}$")
    enabled: bool = Field(strict=True)
    metric: Literal[
        "temperature_c", "humidity_pct", "power_1_voltage_v",
        "power_1_current_a", "power_total_w",
    ]
    direction: Literal["high", "low"]
    severity: Literal["info", "warning", "critical"]
    threshold: float = Field(ge=-1000000, le=1000000, allow_inf_nan=False, strict=True)
    hysteresis: float = Field(ge=0, le=1000000, allow_inf_nan=False, strict=True)
    delay_ms: int = Field(ge=0, le=3600000, strict=True)


class AlarmRulePublic(AlarmRuleInput):
    index: int = Field(ge=0, le=5)


class AlarmRulesPublic(BaseModel):
    rules: list[AlarmRulePublic]


class AlarmRuleSyncStatus(BaseModel):
    synchronized: bool
    mismatched_indexes: list[int]


def _validated_rules(raw: object) -> list[dict]:
    if not isinstance(raw, list) or len(raw) != 6:
        raise HTTPException(status_code=502, detail="Invalid alarm rule response")
    try:
        rules = [AlarmRulePublic.model_validate(item) for item in raw]
    except Exception as error:
        raise HTTPException(status_code=502, detail="Invalid alarm rule response") from error
    if {rule.index for rule in rules} != set(range(6)):
        raise HTTPException(status_code=502, detail="Invalid alarm rule response")
    return [rule.model_dump() for rule in sorted(rules, key=lambda rule: rule.index)]


def _same_rule(echoed: dict, proposed: dict) -> bool:
    for key, value in proposed.items():
        if key in {"threshold", "hysteresis"}:
            if not math.isclose(echoed[key], value, rel_tol=1e-6, abs_tol=1e-5):
                return False
        elif echoed[key] != value:
            return False
    return True


def _configuration(
    device_id: int, current_user: User, db: Session
) -> DeviceAlarmConfiguration:
    _accessible_device(db, device_id, current_user.id, DeviceAction.CHANGE_CONFIGURATION)
    existing = db.get(DeviceAlarmConfiguration, device_id)
    if existing is not None and existing.owner_user_id == current_user.id:
        return existing
    if existing is not None:
        raise HTTPException(status_code=409, detail="Alarm configuration owner mismatch")
    # First use after association: adopt the device's existing six-rule
    # profile. Subsequent reads use the backend copy, including offline.
    response = _send_owner_command(
        device_id=device_id,
        command="get_alarm_rules",
        current_user=current_user,
        db=db,
    )
    if response.result != "ack" or not isinstance(response.data, dict):
        raise HTTPException(status_code=502, detail="Alarm rules unavailable")
    rules = _validated_rules(response.data.get("rules"))
    existing = DeviceAlarmConfiguration(
        device_id=device_id, owner_user_id=current_user.id, rules=rules
    )
    db.add(existing)
    db.commit()
    return existing


@router.get("/{device_id}/alarm-rules", response_model=AlarmRulesPublic)
def get_alarm_rules(
    device_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    configuration = _configuration(device_id, current_user, db)
    return AlarmRulesPublic(rules=_validated_rules(configuration.rules))


@router.get("/{device_id}/alarm-rules/sync-status", response_model=AlarmRuleSyncStatus)
def get_alarm_rule_sync_status(
    device_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    configuration = _configuration(device_id, current_user, db)
    expected = _validated_rules(configuration.rules)
    response = _send_owner_command(
        device_id=device_id,
        command="get_alarm_rules",
        current_user=current_user,
        db=db,
    )
    if response.result != "ack" or not isinstance(response.data, dict):
        raise HTTPException(status_code=502, detail="Alarm rules unavailable")
    actual = _validated_rules(response.data.get("rules"))
    differences = [
        index for index in range(6)
        if not _same_rule(actual[index], expected[index])
    ]
    return AlarmRuleSyncStatus(
        synchronized=not differences,
        mismatched_indexes=differences,
    )


@router.post("/{device_id}/alarm-rules/{index}", response_model=AlarmRulesPublic)
def set_alarm_rule(
    device_id: int,
    index: int,
    request: AlarmRuleInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not 0 <= index < 6:
        raise HTTPException(status_code=422, detail="Invalid alarm rule index")
    configuration = _configuration(device_id, current_user, db)
    proposed = {"index": index, **request.model_dump()}
    response = _send_owner_command(
        device_id=device_id,
        command="set_alarm_rule",
        current_user=current_user,
        db=db,
        parameters=proposed,
    )
    if response.result != "ack" or not isinstance(response.data, dict):
        raise HTTPException(status_code=409, detail="Alarm rule not accepted by device")
    echoed = _validated_rules([
        *(rule for rule in configuration.rules if rule["index"] != index),
        response.data.get("rule"),
    ])[index]
    if not _same_rule(echoed, proposed):
        raise HTTPException(status_code=502, detail="Alarm rule confirmation mismatch")
    updated = list(configuration.rules)
    updated[index] = echoed
    configuration.rules = updated
    db.commit()
    device = _accessible_device(db, device_id, current_user.id, DeviceAction.CHANGE_CONFIGURATION)
    mqtt_manager._alarm_rule_recovery.invalidate(device.mqtt_device_id, device.device_uid)
    return AlarmRulesPublic(rules=_validated_rules(configuration.rules))
