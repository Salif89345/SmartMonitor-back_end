import json
import unittest

from types import SimpleNamespace
from unittest.mock import Mock

import paho.mqtt.client as mqtt

from app.mqtt_client import MqttManager
from app.mqtt_contract import (
    MqttContractError,
    build_command_payload,
    validate_command_payload,
    validate_response_payload,
)


class _Message:
    def __init__(self, topic: str, payload: dict):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")


class _LoopbackClient:
    def __init__(self, manager, response_factory):
        self.manager = manager
        self.response_factory = response_factory
        self.last_topic = None
        self.last_payload = None

    def publish(self, topic, payload, qos, retain):
        self.last_topic = topic
        self.last_payload = json.loads(payload)

        response = self.response_factory(self.last_payload)

        self.manager._on_message(
            None,
            None,
            _Message(
                "smartmonitor/atelier/response",
                response,
            ),
        )

        return SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)


class MqttContractUnitTests(unittest.TestCase):
    def test_valid_ping_command(self):
        payload = build_command_payload(
            request_id="ping-001",
            command="ping",
            parameters={},
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["request_id"], "ping-001")
        self.assertEqual(payload["command"], "ping")
        self.assertEqual(payload["parameters"], {})

    def test_invalid_ping_and_missing_schema_version(self):
        cases = (
            {
                "request_id": "ping-002",
                "command": "ping",
                "parameters": {},
            },
            {
                "schema_version": 1,
                "request_id": "ping-003",
                "command": "ping",
                "parameters": [],
            },
        )

        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(
                MqttContractError
            ):
                validate_command_payload(payload)

    def test_ack_and_nack_contracts(self):
        ack = {
            "schema_version": 1,
            "request_id": "ping-004",
            "result": "ack",
            "error_code": None,
            "message": "pong",
        }
        nack = {
            "schema_version": 1,
            "request_id": "ping-005",
            "result": "nack",
            "error_code": "unsupported_command",
            "message": "Commande non supportee",
        }

        self.assertEqual(
            validate_response_payload(ack)["result"],
            "ack",
        )
        self.assertEqual(
            validate_response_payload(nack)["result"],
            "nack",
        )

    def test_request_id_correlation(self):
        response = {
            "schema_version": 1,
            "request_id": "response-002",
            "result": "ack",
            "error_code": None,
            "message": "pong",
        }

        with self.assertRaises(MqttContractError):
            validate_response_payload(
                response,
                expected_request_id="request-001",
            )


class MqttContractIntegrationTests(unittest.TestCase):
    def _manager(self, response_factory):
        manager = MqttManager()
        manager.is_connected = Mock(return_value=True)
        manager.get_device_status = Mock(return_value="online")
        manager._persist_device_event = Mock()
        manager.client = _LoopbackClient(manager, response_factory)
        return manager

    def test_ping_ack_round_trip(self):
        def response(command):
            return {
                "schema_version": 1,
                "request_id": command["request_id"],
                "result": "ack",
                "error_code": None,
                "message": "pong",
            }

        manager = self._manager(response)
        result = manager.send_command(
            mqtt_device_id="atelier",
            command="ping",
            parameters={},
            timeout=0.1,
        )

        self.assertEqual(result["result"], "ack")
        self.assertEqual(manager.client.last_topic, "smartmonitor/atelier/command")
        self.assertEqual(manager.client.last_payload["schema_version"], 1)
        self.assertEqual(result["request_id"], manager.client.last_payload["request_id"])

    def test_ping_nack_round_trip(self):
        def response(command):
            return {
                "schema_version": 1,
                "request_id": command["request_id"],
                "result": "nack",
                "error_code": "unsupported_command",
                "message": "Commande non supportee",
            }

        manager = self._manager(response)
        result = manager.send_command(
            mqtt_device_id="atelier",
            command="ping_invalid",
            timeout=0.1,
        )

        self.assertEqual(result["result"], "nack")
        self.assertEqual(result["error_code"], "unsupported_command")

    def test_response_without_schema_version_is_rejected_immediately(self):
        def response(command):
            return {
                "request_id": command["request_id"],
                "result": "ack",
                "error_code": None,
                "message": "pong",
            }

        manager = self._manager(response)

        with self.assertRaises(ValueError) as raised:
            manager.send_command(
                mqtt_device_id="atelier",
                command="ping",
                timeout=0.1,
            )

        self.assertIn("schema_version", str(raised.exception))

    def test_mismatched_request_id_times_out(self):
        def response(command):
            return {
                "schema_version": 1,
                "request_id": command["request_id"] + "-other",
                "result": "ack",
                "error_code": None,
                "message": "pong",
            }

        manager = self._manager(response)

        with self.assertRaises(TimeoutError):
            manager.send_command(
                mqtt_device_id="atelier",
                command="ping",
                timeout=0.01,
            )


if __name__ == "__main__":
    unittest.main()
