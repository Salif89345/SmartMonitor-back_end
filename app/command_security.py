import base64
import hashlib
import hmac
import json
import secrets
import time

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.device_authorization import (
    DeviceAction,
    require_device_action,
)
from app.device_identity import is_canonical_device_uid
from app.mqtt_contract import build_command_payload


SECURE_WRAPPER_COMMAND = "secure_execute"
COMMAND_AUTH_ALGORITHM = "HMAC-SHA256"
COMMAND_AUTH_VERSION = 1
COMMAND_AUTH_TTL_SECONDS = 30
LOCAL_HISTORY_RECOVERY_SERVICE = "local_history_recovery"


class CommandRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class CommandPolicy:
    action: DeviceAction
    risk: CommandRisk
    cryptographic_protection: bool
    confirmation_required: bool = False


@dataclass(frozen=True)
class AuthorizedDeviceCommand:
    device_uid: str
    command: str
    policy: CommandPolicy


_DIAGNOSTIC = DeviceAction.SEND_DIAGNOSTIC_COMMAND
_CONFIGURATION = DeviceAction.CHANGE_CONFIGURATION
_MAINTENANCE = DeviceAction.RUN_MAINTENANCE


COMMAND_POLICIES = {
    "ping": CommandPolicy(
        _DIAGNOSTIC,
        CommandRisk.LOW,
        cryptographic_protection=False,
    ),
    "get_status": CommandPolicy(
        _DIAGNOSTIC,
        CommandRisk.MEDIUM,
        cryptographic_protection=True,
    ),
    "get_config": CommandPolicy(
        _DIAGNOSTIC,
        CommandRisk.MEDIUM,
        cryptographic_protection=True,
    ),
    "get_history": CommandPolicy(
        _DIAGNOSTIC,
        CommandRisk.MEDIUM,
        cryptographic_protection=True,
    ),
    "get_daily_energy": CommandPolicy(
        _DIAGNOSTIC,
        CommandRisk.MEDIUM,
        cryptographic_protection=True,
    ),
    "get_alarm_rules": CommandPolicy(
        _DIAGNOSTIC,
        CommandRisk.MEDIUM,
        cryptographic_protection=True,
    ),
    "ack_history": CommandPolicy(
        _MAINTENANCE,
        CommandRisk.HIGH,
        cryptographic_protection=True,
    ),
    "set_energy_tariff": CommandPolicy(
        _CONFIGURATION,
        CommandRisk.HIGH,
        cryptographic_protection=True,
    ),
    "set_alarm_rule": CommandPolicy(
        _CONFIGURATION,
        CommandRisk.HIGH,
        cryptographic_protection=True,
    ),
    "set_config": CommandPolicy(
        _CONFIGURATION,
        CommandRisk.CRITICAL,
        cryptographic_protection=True,
        confirmation_required=True,
    ),
    "reset_alarm_rules": CommandPolicy(
        _CONFIGURATION,
        CommandRisk.CRITICAL,
        cryptographic_protection=True,
        confirmation_required=True,
    ),
    "ota_update": CommandPolicy(
        _MAINTENANCE,
        CommandRisk.CRITICAL,
        cryptographic_protection=True,
        confirmation_required=True,
    ),
    "ota_activate": CommandPolicy(
        _MAINTENANCE,
        CommandRisk.CRITICAL,
        cryptographic_protection=True,
        confirmation_required=True,
    ),
    # Reserved names. They are protected before future routes or firmware
    # handlers are added, but this milestone does not expose them.
    "restart": CommandPolicy(
        _MAINTENANCE,
        CommandRisk.CRITICAL,
        cryptographic_protection=True,
        confirmation_required=True,
    ),
    "factory_reset": CommandPolicy(
        _MAINTENANCE,
        CommandRisk.CRITICAL,
        cryptographic_protection=True,
        confirmation_required=True,
    ),
}


SERVICE_COMMAND_ALLOWLIST = {
    LOCAL_HISTORY_RECOVERY_SERVICE: frozenset(
        {
            "get_history",
            "ack_history",
        }
    ),
}


class CommandKeyError(RuntimeError):
    """Raised when the per-device command signing key is unavailable."""


def expected_confirmation(
    command: str,
    device_uid: str,
) -> str:
    return f"{command}:{device_uid}"


def authorize_device_command(
    *,
    role: str,
    device_uid: str,
    command: str,
    confirmation: str | None = None,
) -> AuthorizedDeviceCommand:
    if not is_canonical_device_uid(device_uid):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device hardware identity is not configured",
        )

    policy = COMMAND_POLICIES.get(command)

    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported device command",
        )

    require_device_action(role, policy.action)

    if policy.confirmation_required:
        expected = expected_confirmation(command, device_uid)

        if not hmac.compare_digest(confirmation or "", expected):
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail="Sensitive command confirmation required",
            )

    return AuthorizedDeviceCommand(
        device_uid=device_uid,
        command=command,
        policy=policy,
    )


def authorize_service_device_command(
    *,
    service: str,
    device_uid: str,
    command: str,
) -> AuthorizedDeviceCommand:
    """Authorize one narrowly-scoped backend service command.

    Service authorization is deliberately separate from user authorization:
    an automatic recovery job must never be recorded as if a human owner had
    initiated it. Unknown services and commands fail closed.
    """

    if not is_canonical_device_uid(device_uid):
        raise ValueError(
            "Device hardware identity is not configured"
        )

    allowed_commands = SERVICE_COMMAND_ALLOWLIST.get(service)

    if allowed_commands is None or command not in allowed_commands:
        raise PermissionError(
            "Service is not authorized for this device command"
        )

    policy = COMMAND_POLICIES.get(command)

    if policy is None:
        raise ValueError("Unsupported device command")

    return AuthorizedDeviceCommand(
        device_uid=device_uid,
        command=command,
        policy=policy,
    )


def _read_device_key(
    keys_path: str | None,
    device_uid: str,
) -> tuple[str, bytes]:
    if not keys_path:
        raise CommandKeyError(
            "Command authorization key store is not configured"
        )

    try:
        document = json.loads(
            Path(keys_path).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandKeyError(
            "Command authorization key store is unavailable"
        ) from exc

    if document.get("version") != COMMAND_AUTH_VERSION:
        raise CommandKeyError(
            "Unsupported command authorization key store"
        )

    entry = document.get("devices", {}).get(device_uid)

    if not isinstance(entry, dict):
        raise CommandKeyError(
            "No command authorization key for device"
        )

    key_id = entry.get("key_id")
    key_hex = entry.get("key_hex")

    if (
        not isinstance(key_id, str)
        or not key_id
        or len(key_id) > 31
        or not isinstance(key_hex, str)
        or len(key_hex) != 64
    ):
        raise CommandKeyError(
            "Invalid command authorization key entry"
        )

    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise CommandKeyError(
            "Invalid command authorization key encoding"
        ) from exc

    if len(key) != 32:
        raise CommandKeyError(
            "Command authorization key must contain 256 bits"
        )

    return key_id, key


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _signature_input(
    *,
    key_id: str,
    device_uid: str,
    issued_at: int,
    expires_at: int,
    nonce: str,
    payload: str,
) -> bytes:
    return (
        "SMCMD1\n"
        f"{key_id}\n"
        f"{device_uid}\n"
        f"{issued_at}\n"
        f"{expires_at}\n"
        f"{nonce}\n"
        f"{payload}"
    ).encode("utf-8")


def build_authorized_command_payload(
    *,
    authorization: AuthorizedDeviceCommand,
    request_id: str,
    parameters: dict[str, Any] | None,
    keys_path: str | None,
    now: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    if not isinstance(authorization, AuthorizedDeviceCommand):
        raise TypeError("A command authorization decision is required")

    inner_payload = build_command_payload(
        request_id=request_id,
        command=authorization.command,
        parameters=parameters,
    )

    if not authorization.policy.cryptographic_protection:
        return inner_payload

    key_id, key = _read_device_key(
        keys_path,
        authorization.device_uid,
    )
    issued_at = int(time.time()) if now is None else int(now)
    expires_at = issued_at + COMMAND_AUTH_TTL_SECONDS
    command_nonce = nonce or secrets.token_hex(16)

    if len(command_nonce) != 32 or any(
        character not in "0123456789abcdefABCDEF"
        for character in command_nonce
    ):
        raise ValueError("Command nonce must be 128-bit hexadecimal")

    serialized_inner = json.dumps(
        inner_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    encoded_inner = _base64url(serialized_inner)
    signature_input = _signature_input(
        key_id=key_id,
        device_uid=authorization.device_uid,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=command_nonce,
        payload=encoded_inner,
    )
    signature = hmac.new(
        key,
        signature_input,
        hashlib.sha256,
    ).hexdigest()

    return build_command_payload(
        request_id=request_id,
        command=SECURE_WRAPPER_COMMAND,
        parameters={
            "version": COMMAND_AUTH_VERSION,
            "algorithm": COMMAND_AUTH_ALGORITHM,
            "key_id": key_id,
            "device_uid": authorization.device_uid,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "nonce": command_nonce,
            "payload": encoded_inner,
            "mac": signature,
        },
    )
