import argparse
import json
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from app.settings import (
    MQTT_CA_CERT_PATH,
    MQTT_HOST,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_TLS_ENABLED,
    MQTT_USERNAME,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("activate", "reset"))
    parser.add_argument("--device", default="atelier")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    request_id = f"sm034-{args.action}-{uuid.uuid4()}"
    command = "set_alarm_rule" if args.action == "activate" else "reset_alarm_rules"
    parameters = (
        {
            "index": 0,
            "id": "sm034_temperature_test",
            "enabled": True,
            "metric": "temperature_c",
            "direction": "high",
            "severity": "critical",
            "threshold": 0.0,
            "hysteresis": 1.0,
            "delay_ms": 1000,
        }
        if args.action == "activate"
        else {}
    )

    response_event = threading.Event()
    state_event = threading.Event()
    failure: list[str] = []

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"sm034-smoke-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
    )
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    if MQTT_TLS_ENABLED:
        client.tls_set(ca_certs=MQTT_CA_CERT_PATH)

    response_topic = f"smartmonitor/{args.device}/response"
    state_topic = f"smartmonitor/{args.device}/state"

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            failure.append(f"MQTT connection refused: {reason_code}")
            response_event.set()
            state_event.set()
            return
        client.subscribe([(response_topic, 0), (state_topic, 0)])
        client.publish(
            f"smartmonitor/{args.device}/command",
            json.dumps(
                {
                    "schema_version": 1,
                    "request_id": request_id,
                    "command": command,
                    "parameters": parameters,
                },
                separators=(",", ":"),
            ),
            qos=0,
            retain=False,
        )

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        if message.topic == response_topic and payload.get("request_id") == request_id:
            if payload.get("result") != "ack":
                failure.append(f"Command rejected: {payload}")
            response_event.set()
            return

        if message.topic != state_topic:
            return
        alarms = payload.get("alarms")
        if not isinstance(alarms, dict):
            return
        items = alarms.get("items")
        if not isinstance(items, list):
            return

        if args.action == "activate":
            if any(
                item.get("id") == "sm034_temperature_test"
                and item.get("state") in {"active", "pending_clear"}
                for item in items
                if isinstance(item, dict)
            ):
                state_event.set()
        elif alarms.get("enabled_rule_count") == 0 and alarms.get("active_count") == 0:
            state_event.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 30)
    client.loop_start()
    try:
        deadline = time.monotonic() + args.timeout
        if not response_event.wait(max(0.0, deadline - time.monotonic())):
            failure.append("Command response timeout")
        if not failure and not state_event.wait(max(0.0, deadline - time.monotonic())):
            failure.append("Expected alarm state timeout")
    finally:
        client.disconnect()
        client.loop_stop()

    if failure:
        print(f"[SM-034] FAIL: {failure[0]}")
        return 1
    print(f"[SM-034] {args.action.upper()} PASS | device={args.device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
