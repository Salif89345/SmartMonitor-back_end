import json
import os
import threading
import uuid

from dotenv import load_dotenv
import paho.mqtt.client as mqtt


load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

MQTT_CLIENT_ID = os.getenv(
    "MQTT_CLIENT_ID",
    "smartmonitor-backend-v1",
)

MQTT_KEEPALIVE = int(
    os.getenv("MQTT_KEEPALIVE", "30")
)

MQTT_SUBSCRIPTIONS = (
    "smartmonitor/+/state",
    "smartmonitor/+/status",
    "smartmonitor/+/response",
)


class MqttManager:
    def __init__(self):
        self._connected = threading.Event()

        self._state_lock = threading.Lock()
        self._last_error: str | None = None
        self._received_messages = 0
        self._last_message_topic: str | None = None

        self._pending_lock = threading.Lock()
        self._pending_responses: dict[str, dict] = {}

        self.client = mqtt.Client(
            callback_api_version=
                mqtt.CallbackAPIVersion.VERSION2,
            client_id=MQTT_CLIENT_ID,
            protocol=mqtt.MQTTv311,
        )

        if MQTT_USERNAME:
            self.client.username_pw_set(
                MQTT_USERNAME,
                MQTT_PASSWORD,
            )

        self.client.reconnect_delay_set(
            min_delay=1,
            max_delay=30,
        )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message

    def _on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ):
        if reason_code != 0:
            self._connected.clear()

            with self._state_lock:
                self._last_error = (
                    f"Connection refused: {reason_code}"
                )

            print(
                "[MQTT] Connection failed:",
                reason_code,
            )
            return

        self._connected.set()

        with self._state_lock:
            self._last_error = None

        print("[MQTT] Backend connected")

        for topic in MQTT_SUBSCRIPTIONS:
            result, message_id = client.subscribe(
                topic,
                qos=0,
            )

            if result == mqtt.MQTT_ERR_SUCCESS:
                print(
                    "[MQTT] Subscription requested:",
                    topic,
                )
            else:
                print(
                    "[MQTT] Subscription request failed:",
                    topic,
                    "| code:",
                    result,
                )

    def _on_subscribe(
        self,
        client,
        userdata,
        message_id,
        reason_code_list,
        properties,
    ):
        failures = [
            reason_code
            for reason_code in reason_code_list
            if reason_code.is_failure
        ]

        if failures:
            print(
                "[MQTT] Subscription refused:",
                failures,
            )

            with self._state_lock:
                self._last_error = (
                    f"Subscription refused: {failures}"
                )

            return

        print("[MQTT] Subscription confirmed")

    def _on_message(
        self,
        client,
        userdata,
        message,
    ):
        with self._state_lock:
            self._received_messages += 1
            self._last_message_topic = message.topic

        print(
            "[MQTT] Message received:",
            message.topic,
            "| bytes:",
            len(message.payload),
        )

        if not message.topic.endswith("/response"):
            return

        try:
            payload = json.loads(
                message.payload.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            print(
                "[MQTT] Invalid response payload:",
                message.topic,
            )
            return

        request_id = payload.get("request_id")

        if not isinstance(request_id, str):
            return

        with self._pending_lock:
            pending = self._pending_responses.get(
                request_id
            )

            if pending is None:
                return

            if (
                message.topic
                != pending["response_topic"]
            ):
                return

            pending["response"] = payload
            pending["event"].set()

    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ):
        self._connected.clear()

        if reason_code != 0:
            with self._state_lock:
                self._last_error = (
                    f"Disconnected: {reason_code}"
                )

        print(
            "[MQTT] Backend disconnected:",
            reason_code,
        )

    def start(self):
        print(
            f"[MQTT] Connecting to "
            f"{MQTT_HOST}:{MQTT_PORT}"
        )

        self.client.connect_async(
            MQTT_HOST,
            MQTT_PORT,
            MQTT_KEEPALIVE,
        )

        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

    def send_command(
        self,
        mqtt_device_id: str,
        command: str,
        parameters: dict | None = None,
        timeout: float = 5.0,
    ) -> dict:
        if not self.is_connected():
            raise RuntimeError(
                "MQTT backend is not connected"
            )

        request_id = str(uuid.uuid4())

        command_topic = (
            f"smartmonitor/"
            f"{mqtt_device_id}/command"
        )

        response_topic = (
            f"smartmonitor/"
            f"{mqtt_device_id}/response"
        )

        payload = {
            "schema_version": 1,
            "request_id": request_id,
            "command": command,
            "parameters": parameters or {},
        }

        pending = {
            "event": threading.Event(),
            "response": None,
            "response_topic": response_topic,
        }

        with self._pending_lock:
            self._pending_responses[
                request_id
            ] = pending

        try:
            message_info = self.client.publish(
                command_topic,
                json.dumps(payload),
                qos=0,
                retain=False,
            )

            if (
                message_info.rc
                != mqtt.MQTT_ERR_SUCCESS
            ):
                raise RuntimeError(
                    "MQTT command publish failed"
                )

            print(
                "[MQTT] Command published:",
                command_topic,
                "| request_id:",
                request_id,
            )

            response_received = pending[
                "event"
            ].wait(timeout)

            if not response_received:
                raise TimeoutError(
                    "Device response timeout"
                )

            response = pending["response"]

            if not isinstance(response, dict):
                raise ValueError(
                    "Invalid device response"
                )

            if (
                response.get("request_id")
                != request_id
            ):
                raise ValueError(
                    "Invalid response request_id"
                )

            if response.get("result") not in (
                "ack",
                "nack",
            ):
                raise ValueError(
                    "Invalid response result"
                )

            if not isinstance(
                response.get("message"),
                str,
            ):
                raise ValueError(
                    "Invalid response message"
                )

            return response

        finally:
            with self._pending_lock:
                self._pending_responses.pop(
                    request_id,
                    None,
                )

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def status(self) -> dict:
        with self._state_lock:
            return {
                "connected": self.is_connected(),
                "host": MQTT_HOST,
                "port": MQTT_PORT,
                "client_id": MQTT_CLIENT_ID,
                "subscriptions":
                    list(MQTT_SUBSCRIPTIONS),
                "received_messages":
                    self._received_messages,
                "last_message_topic":
                    self._last_message_topic,
                "last_error":
                    self._last_error,
            }


mqtt_manager = MqttManager()