from enum import Enum

from fastapi import HTTPException, status


class DeviceAction(str, Enum):
    """Actions protected by the SmartMonitor device access policy."""

    READ_DEVICE = "read_device"
    READ_HISTORY = "read_history"
    READ_ALARMS = "read_alarms"
    ACKNOWLEDGE_ALARM = "acknowledge_alarm"
    READ_EVENTS = "read_events"
    SEND_DIAGNOSTIC_COMMAND = "send_diagnostic_command"
    CHANGE_CONFIGURATION = "change_configuration"
    RUN_MAINTENANCE = "run_maintenance"
    MANAGE_MEMBERS = "manage_members"


_MEMBER_ACTIONS = frozenset(
    {
        DeviceAction.READ_DEVICE,
        DeviceAction.READ_HISTORY,
        DeviceAction.READ_ALARMS,
        DeviceAction.ACKNOWLEDGE_ALARM,
        DeviceAction.READ_EVENTS,
    }
)

_OWNER_ACTIONS = frozenset(DeviceAction)

_ACTIONS_BY_ROLE = {
    "owner": _OWNER_ACTIONS,
    "member": _MEMBER_ACTIONS,
}

_OWNER_ONLY_ACTIONS = _OWNER_ACTIONS - _MEMBER_ACTIONS


def is_device_action_allowed(
    role: str,
    action: DeviceAction,
) -> bool:
    """Return False for unknown roles so authorization fails closed."""

    return action in _ACTIONS_BY_ROLE.get(
        role,
        frozenset(),
    )


def require_device_action(
    role: str,
    action: DeviceAction,
) -> None:
    """Reject a device action that is not granted to the membership role."""

    if is_device_action_allowed(role, action):
        return

    detail = (
        "Owner access required"
        if action in _OWNER_ONLY_ACTIONS
        else "Device access denied"
    )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )
