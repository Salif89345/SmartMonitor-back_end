import json
import unittest

from unittest.mock import Mock

from app.mqtt_client import MqttManager


class Message:
    def __init__(self, topic: str, payload: dict):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")


class MqttIngestionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.manager = MqttManager()
        self.manager._ingestion_accepting = True

    def tearDown(self):
        self.manager._stop_ingestion_worker()

    def test_callback_enqueues_state_without_calling_handler(self):
        self.manager._handle_state_message = Mock()

        self.manager._on_message(
            None,
            None,
            Message(
                "smartmonitor/atelier/state",
                {"schema_version": 1},
            ),
        )

        self.manager._handle_state_message.assert_not_called()
        task = self.manager._ingestion_queue.get_nowait()
        self.assertEqual(task.kind, "state")
        self.assertEqual(task.topic, "smartmonitor/atelier/state")
        self.manager._ingestion_queue.task_done()

    def test_worker_survives_a_failed_task_and_processes_next_one(self):
        self.manager._handle_state_message = Mock(
            side_effect=[RuntimeError("database unavailable"), None]
        )
        self.manager._start_ingestion_worker()

        self.manager._enqueue_ingestion_message(
            "state", "smartmonitor/atelier/state", b"{}"
        )
        self.manager._enqueue_ingestion_message(
            "state", "smartmonitor/atelier/state", b"{}"
        )
        self.manager._ingestion_queue.join()

        self.assertEqual(
            self.manager._handle_state_message.call_count,
            2,
        )

    def test_full_queue_drops_message_and_counts_backpressure(self):
        self.manager._ingestion_queue.maxsize = 1
        self.manager._enqueue_ingestion_message(
            "state", "smartmonitor/atelier/state", b"first"
        )
        self.manager._enqueue_ingestion_message(
            "state", "smartmonitor/atelier/state", b"second"
        )

        self.assertEqual(
            self.manager.status()["ingestion"]["dropped_messages"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
