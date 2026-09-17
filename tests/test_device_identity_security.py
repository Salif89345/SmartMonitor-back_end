import json
import unittest

from unittest.mock import patch

from app.device_identity import (
    DeviceIdentityError,
    certificate_common_name_for_device,
    is_canonical_device_uid,
    mqtt_client_id_for_device,
    validate_state_identity,
)
from app.mqtt_client import MqttManager


DEVICE_UID = "SM-A1B2C3D4E5F6"
MQTT_DEVICE_ID = "atelier"


class Message:
    topic = f"smartmonitor/{MQTT_DEVICE_ID}/state"

    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")


def state_payload(**overrides) -> dict:
    payload = {
        "schema_version": 2,
        "device": MQTT_DEVICE_ID,
        "mqtt_device_id": MQTT_DEVICE_ID,
        "device_uid": DEVICE_UID,
    }
    payload.update(overrides)
    return payload


class DeviceIdentityUnitTests(unittest.TestCase):
    def test_canonical_device_uid_is_accepted(self):
        self.assertTrue(
            is_canonical_device_uid(DEVICE_UID)
        )

    def test_noncanonical_device_uids_are_rejected(self):
        for value in (
            None,
            "",
            "SM-A1B2C3",
            "sm-A1B2C3D4E5F6",
            "SM-a1b2c3d4e5f6",
            "SM-A1B2C3D4E5G6",
        ):
            with self.subTest(value=value):
                self.assertFalse(
                    is_canonical_device_uid(value)
                )

    def test_client_id_and_future_certificate_cn_share_uid(self):
        self.assertEqual(
            mqtt_client_id_for_device(DEVICE_UID),
            DEVICE_UID,
        )
        self.assertEqual(
            certificate_common_name_for_device(
                DEVICE_UID
            ),
            DEVICE_UID,
        )

    def test_schema_v2_requires_complete_identity(self):
        with self.assertRaisesRegex(
            DeviceIdentityError,
            "device_uid",
        ):
            validate_state_identity(
                topic_mqtt_device_id=MQTT_DEVICE_ID,
                payload={
                    "mqtt_device_id": MQTT_DEVICE_ID,
                },
                schema_version=2,
            )

    def test_routing_identity_must_match_topic(self):
        with self.assertRaisesRegex(
            DeviceIdentityError,
            "does not match topic",
        ):
            validate_state_identity(
                topic_mqtt_device_id=MQTT_DEVICE_ID,
                payload={
                    "device_uid": DEVICE_UID,
                    "mqtt_device_id": "other",
                },
                schema_version=2,
            )

    def test_schema_v1_legacy_payload_remains_compatible(self):
        self.assertIsNone(
            validate_state_identity(
                topic_mqtt_device_id=MQTT_DEVICE_ID,
                payload={},
                schema_version=1,
            )
        )


class DeviceIdentityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.manager = MqttManager()

    @patch("app.mqtt_client.live_state_store.update")
    def test_valid_identity_reaches_live_state(
        self,
        update,
    ):
        self.manager._handle_state_message(
            Message(state_payload())
        )
        update.assert_called_once()

    @patch("app.mqtt_client.live_state_store.update")
    def test_missing_uid_is_rejected_before_live_state(
        self,
        update,
    ):
        payload = state_payload()
        payload.pop("device_uid")
        self.manager._handle_state_message(
            Message(payload)
        )
        update.assert_not_called()

    @patch("app.mqtt_client.live_state_store.update")
    def test_mismatched_routing_identity_is_rejected(
        self,
        update,
    ):
        self.manager._handle_state_message(
            Message(
                state_payload(
                    mqtt_device_id="other"
                )
            )
        )
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
