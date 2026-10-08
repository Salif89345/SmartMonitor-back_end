"""Create one harmless alarm-rule drift on Dev3 for SM-032 recovery testing.

This bench-only tool changes the threshold of an already disabled rule. It
never enables an alarm, touches another ESP, or prints broker credentials.
"""

import argparse
import json
import threading
import uuid

import paho.mqtt.client as mqtt
from sqlalchemy import select

from app.command_security import (
    authorize_device_command,
    build_authorized_command_payload,
)
from app.database import SessionLocal
from app.device_transfer_guard import require_active_transfer_access
from app.models import Device, DeviceAlarmConfiguration, DeviceMembership
from app.mqtt_contract import validate_response_payload
from app.settings import (
    COMMAND_AUTH_KEYS_PATH,
    MQTT_CA_CERT_PATH,
    MQTT_HOST,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_TLS_ENABLED,
    MQTT_USERNAME,
)


DEV3_UID = "SM-E8B817077000"
DEV3_MQTT_ID = "sm012-spare"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-dev3", action="store_true", required=True)
    args = parser.parse_args()
    if not args.confirm_dev3:
        return 2

    with SessionLocal() as db:
        device = db.scalar(select(Device).where(
            Device.device_uid == DEV3_UID,
            Device.mqtt_device_id == DEV3_MQTT_ID,
        ))
        if device is None:
            raise RuntimeError("Dev3 is not registered with the expected identity")
        config = db.get(DeviceAlarmConfiguration, device.id)
        if config is None or not isinstance(config.rules, list) or len(config.rules) != 6:
            raise RuntimeError("Dev3 alarm master copy is unavailable")
        role = db.scalar(select(DeviceMembership.role).where(
            DeviceMembership.device_id == device.id,
            DeviceMembership.user_id == config.owner_user_id,
        ))
        if role != "owner":
            raise RuntimeError("Alarm master copy does not belong to current owner")
        require_active_transfer_access(
            db, device_id=device.id, user_id=config.owner_user_id, role=role
        )
        original = next((r for r in config.rules if r.get("index") == 0), None)
        if not isinstance(original, dict) or original.get("enabled") is not False:
            raise RuntimeError("Rule 0 must already be disabled")
        changed = dict(original)
        changed["threshold"] = float(original["threshold"]) + 1.0
        if changed["threshold"] > 1000000:
            changed["threshold"] = float(original["threshold"]) - 1.0

    request_id = f"sm032-{uuid.uuid4()}"
    decision = authorize_device_command(
        role="owner", device_uid=DEV3_UID, command="set_alarm_rule"
    )
    payload = build_authorized_command_payload(
        authorization=decision,
        request_id=request_id,
        parameters=changed,
        keys_path=COMMAND_AUTH_KEYS_PATH,
    )
    connected = threading.Event()
    replied = threading.Event()
    result = {}
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"sm032-probe-{uuid.uuid4().hex[:12]}",
        protocol=mqtt.MQTTv311,
    )
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    if MQTT_TLS_ENABLED:
        client.tls_set(ca_certs=MQTT_CA_CERT_PATH)

    response_topic = f"smartmonitor/{DEV3_MQTT_ID}/response"

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(response_topic, qos=0)
        else:
            result["error"] = f"MQTT connect refused: {reason_code}"
        connected.set()

    def on_message(client, userdata, message):
        if message.topic != response_topic:
            return
        try:
            raw = json.loads(message.payload.decode("utf-8"))
            if raw.get("request_id") == request_id:
                result["response"] = validate_response_payload(
                    raw, expected_request_id=request_id
                )
                replied.set()
        except (ValueError, UnicodeDecodeError):
            return

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 30)
    client.loop_start()
    try:
        if not connected.wait(8) or "error" in result:
            raise RuntimeError(result.get("error", "MQTT connect timeout"))
        # Subscription must be acknowledged before publishing the command.
        subscribed = threading.Event()
        client.on_subscribe = lambda *unused: subscribed.set()
        client.subscribe(response_topic, qos=0)
        if not subscribed.wait(5):
            raise RuntimeError("Response subscription timeout")
        info = client.publish(
            f"smartmonitor/{DEV3_MQTT_ID}/command",
            json.dumps(payload, separators=(",", ":")),
            qos=0,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS or not replied.wait(8):
            raise RuntimeError("Signed command response timeout")
    finally:
        client.disconnect()
        client.loop_stop()

    response = result["response"]
    echoed = response.get("data", {}).get("rule")
    if (
        response["result"] != "ack"
        or not isinstance(echoed, dict)
        or echoed.get("enabled") is not False
        or echoed.get("threshold") != changed["threshold"]
    ):
        raise RuntimeError("Dev3 did not confirm the disabled-rule drift")
    print("PASS: Dev3 accepted a signed, disabled-rule threshold change.")
    print("Backend master copy was not changed; automatic recovery can now be tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
