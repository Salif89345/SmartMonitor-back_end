import json
import unittest

from unittest.mock import Mock

from app.mqtt_client import MqttManager


class Message:
    def __init__(self, topic: str, payload):
        self.topic = topic
        self.payload = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )


class OutageMessageTests(unittest.TestCase):
    def setUp(self):
        self.manager = MqttManager()
        self.manager._persist_device_event = Mock()
        self.topic = "smartmonitor/atelier/outage"
        self.report = {
            "schema_version": 1,
            "event": "network_outage",
            "device_id": "atelier",
            "duration_ms": 12_345,
            "wifi_lost": True,
            "mqtt_lost": False,
            "started_at": 1_788_700_000,
            "restored_at": 1_788_712_345,
        }

    def test_valid_report_is_persisted(self):
        self.manager._handle_outage_message(
            Message(self.topic, self.report)
        )

        self.manager._persist_device_event.assert_called_once_with(
            mqtt_device_id="atelier",
            event_type="network_outage",
            data={
                "schema_version": 1,
                "duration_ms": 12_345,
                "wifi_lost": True,
                "mqtt_lost": False,
                "started_at": 1_788_700_000,
                "restored_at": 1_788_712_345,
            },
        )

    def test_unsynchronised_clock_is_accepted(self):
        self.report["started_at"] = None
        self.report["restored_at"] = None

        self.manager._handle_outage_message(
            Message(self.topic, self.report)
        )

        self.manager._persist_device_event.assert_called_once()

    def test_invalid_reports_are_rejected(self):
        invalid_reports = [
            {**self.report, "schema_version": 2},
            {**self.report, "event": "other"},
            {**self.report, "device_id": "another-device"},
            {**self.report, "duration_ms": -1},
            {**self.report, "wifi_lost": 1},
            {**self.report, "started_at": -1},
        ]

        for report in invalid_reports:
            with self.subTest(report=report):
                self.manager._handle_outage_message(
                    Message(self.topic, report)
                )

        self.manager._persist_device_event.assert_not_called()

    def test_invalid_topic_or_payload_is_rejected(self):
        self.manager._handle_outage_message(
            Message("smartmonitor/atelier/other", self.report)
        )
        self.manager._handle_outage_message(
            Message(self.topic, b"not-json")
        )

        self.manager._persist_device_event.assert_not_called()

    def test_router_enqueues_outage_messages(self):
        self.manager._ingestion_accepting = True
        message = Message(self.topic, self.report)

        self.manager._on_message(None, None, message)

        task = self.manager._ingestion_queue.get_nowait()
        self.assertEqual(task.kind, "outage")
        self.assertEqual(task.topic, self.topic)
        self.assertEqual(task.payload, message.payload)
        self.manager._ingestion_queue.task_done()


if __name__ == "__main__":
    unittest.main()