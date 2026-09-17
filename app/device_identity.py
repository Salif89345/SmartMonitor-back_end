import re


DEVICE_UID_PATTERN = re.compile(
    r"^SM-[0-9A-F]{12}$"
)

MQTT_DEVICE_ID_PATTERN = re.compile(
    r"^[^/+#]{1,31}$"
)


class DeviceIdentityError(ValueError):
    """Raised when a device identity contract is inconsistent."""


def is_canonical_device_uid(value: object) -> bool:
    return (
        isinstance(value, str)
        and DEVICE_UID_PATTERN.fullmatch(value)
        is not None
    )


def mqtt_client_id_for_device(
    device_uid: str,
) -> str:
    if not is_canonical_device_uid(device_uid):
        raise DeviceIdentityError(
            "device_uid is not canonical"
        )

    # Convention commune au firmware et au futur certificat client.
    return device_uid


def certificate_common_name_for_device(
    device_uid: str,
) -> str:
    return mqtt_client_id_for_device(device_uid)


def validate_state_identity(
    *,
    topic_mqtt_device_id: str,
    payload: dict,
    schema_version: int,
) -> str | None:
    """
    Validate topic, routing identity and immutable hardware identity.

    Schema V1 remains accepted when both SM-058 identity fields are absent.
    As soon as one identity field is present, the complete contract is required.
    Schema V2 always requires the complete identity contract.
    """
    if (
        not isinstance(topic_mqtt_device_id, str)
        or MQTT_DEVICE_ID_PATTERN.fullmatch(
            topic_mqtt_device_id
        )
        is None
    ):
        raise DeviceIdentityError(
            "MQTT topic identity is invalid"
        )

    device_uid = payload.get("device_uid")
    payload_mqtt_device_id = payload.get(
        "mqtt_device_id"
    )

    identity_present = (
        device_uid is not None
        or payload_mqtt_device_id is not None
    )

    if schema_version == 1 and not identity_present:
        return None

    if not is_canonical_device_uid(device_uid):
        raise DeviceIdentityError(
            "device_uid is missing or invalid"
        )

    if payload_mqtt_device_id != topic_mqtt_device_id:
        raise DeviceIdentityError(
            "mqtt_device_id does not match topic"
        )

    return device_uid
