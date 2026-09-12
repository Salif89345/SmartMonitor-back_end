from collections.abc import Mapping
from typing import Any


MQTT_SCHEMA_VERSION = 1
MAX_REQUEST_ID_LENGTH = 64
MAX_COMMAND_LENGTH = 47
MQTT_RESULTS = frozenset(("ack", "nack"))


class MqttContractError(ValueError):
    """Raised when a command or response violates MQTT contract V1."""


def _validate_request_id(value: Any) -> str:
    if not isinstance(value, str):
        raise MqttContractError("request_id must be a string")

    if not value:
        raise MqttContractError("request_id must not be empty")

    if len(value) > MAX_REQUEST_ID_LENGTH:
        raise MqttContractError("request_id is too long")

    return value


def validate_command_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MqttContractError("command payload must be an object")

    schema_version = payload.get("schema_version")

    if type(schema_version) is not int:
        raise MqttContractError("schema_version must be an integer")

    if schema_version != MQTT_SCHEMA_VERSION:
        raise MqttContractError("unsupported schema_version")

    _validate_request_id(payload.get("request_id"))

    command = payload.get("command")

    if not isinstance(command, str) or not command:
        raise MqttContractError("command must be a non-empty string")

    if len(command) > MAX_COMMAND_LENGTH:
        raise MqttContractError("command is too long")

    parameters = payload.get("parameters", {})

    if not isinstance(parameters, Mapping):
        raise MqttContractError("parameters must be an object")

    return dict(payload)


def build_command_payload(
    *,
    request_id: str,
    command: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": MQTT_SCHEMA_VERSION,
        "request_id": request_id,
        "command": command,
        "parameters": parameters or {},
    }

    return validate_command_payload(payload)


def validate_response_payload(
    payload: Any,
    *,
    expected_request_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MqttContractError("response payload must be an object")

    schema_version = payload.get("schema_version")

    if type(schema_version) is not int:
        raise MqttContractError("schema_version must be an integer")

    if schema_version != MQTT_SCHEMA_VERSION:
        raise MqttContractError("unsupported schema_version")

    request_id = _validate_request_id(payload.get("request_id"))

    if (
        expected_request_id is not None
        and request_id != expected_request_id
    ):
        raise MqttContractError("response request_id does not match")

    result = payload.get("result")

    if result not in MQTT_RESULTS:
        raise MqttContractError("result must be ack or nack")

    error_code = payload.get("error_code")

    if result == "ack" and error_code is not None:
        raise MqttContractError("ack error_code must be null")

    if result == "nack" and (
        not isinstance(error_code, str)
        or not error_code
    ):
        raise MqttContractError("nack error_code must be a non-empty string")

    if not isinstance(payload.get("message"), str):
        raise MqttContractError("message must be a string")

    if "data" in payload and not isinstance(payload["data"], Mapping):
        raise MqttContractError("data must be an object when present")

    return dict(payload)
