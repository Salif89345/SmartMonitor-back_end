import unittest

from sqlalchemy import UniqueConstraint

from app.local_history_recovery import (
    HistoryRecoveryError,
    LocalHistoryRecoveryCoordinator,
    parse_history_page,
    validate_history_ack,
)
from app.models import LocalHistoryRecord


DEVICE_UID = "SM-A1B2C3D4E5F6"


def record(sequence: int) -> dict:
    return {
        "sequence": sequence,
        "captured_at": 1_789_500_000 + sequence,
        "uptime_s": sequence * 60,
        "environment": {
            "temperature_c": 26.5,
            "humidity_pct": 40.0,
        },
        "energy_channels": {
            "power_2": {
                "voltage_v": 235.0,
                "current_a": 0.04,
                "power_w": 4.0,
                "energy_kwh": 59.4,
                "frequency_hz": 50.0,
                "power_factor": 0.45,
            }
        },
    }


def page_response(records: list[dict], cursor: int, has_more: bool) -> dict:
    return {
        "result": "ack",
        "data": {
            "records": records,
            "returned_count": len(records),
            "next_cursor": cursor,
            "has_more": has_more,
        },
    }


class FakeRepository:
    def __init__(self):
        self.pages = []

    def persist_page(self, **kwargs):
        self.pages.append(kwargs)
        return len(kwargs["records"])


class HistoryContractTests(unittest.TestCase):
    def test_page_requires_ordered_bounded_records(self):
        parsed = parse_history_page(
            page_response([record(8), record(9)], 9, True),
            after_sequence=7,
        )

        self.assertEqual(parsed.next_cursor, 9)
        self.assertTrue(parsed.has_more)

        invalid_pages = (
            page_response([record(8), record(9), record(10)], 10, False),
            page_response([record(9), record(8)], 8, False),
            page_response([], 8, False),
            page_response([], 7, True),
        )

        for response in invalid_pages:
            with self.subTest(response=response), self.assertRaises(
                HistoryRecoveryError
            ):
                parse_history_page(response, after_sequence=7)

    def test_ack_must_confirm_exact_persisted_cursor(self):
        validate_history_ack(
            {
                "result": "ack",
                "data": {"through_sequence": 9},
            },
            9,
        )

        with self.assertRaises(HistoryRecoveryError):
            validate_history_ack(
                {
                    "result": "ack",
                    "data": {"through_sequence": 10},
                },
                9,
            )

    def test_model_deduplicates_device_sequence(self):
        constraints = [
            constraint
            for constraint in LocalHistoryRecord.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        matching = [
            constraint
            for constraint in constraints
            if constraint.name
            == "uq_local_history_records_device_sequence"
        ]

        self.assertEqual(len(matching), 1)
        self.assertEqual(
            [column.name for column in matching[0].columns],
            ["device_id", "sequence"],
        )


class HistoryCoordinatorTests(unittest.TestCase):
    def test_recovery_persists_each_page_before_exact_ack(self):
        repository = FakeRepository()
        calls = []

        def sender(mqtt_device_id, device_uid, command, parameters):
            calls.append((mqtt_device_id, device_uid, command, parameters))

            if command == "get_history":
                cursor = parameters["after_sequence"]
                if cursor == 7:
                    return page_response([record(8), record(9)], 9, True)
                if cursor == 9:
                    return page_response([record(10)], 10, False)

            if command == "ack_history":
                return {
                    "result": "ack",
                    "data": {
                        "through_sequence": parameters["through_sequence"]
                    },
                }

            raise AssertionError("unexpected command")

        coordinator = LocalHistoryRecoveryCoordinator(
            command_sender=sender,
            repository=repository,
        )
        coordinator.start()

        try:
            scheduled = coordinator.schedule(
                mqtt_device_id="atelier",
                device_uid=DEVICE_UID,
                local_history={
                    "ready": True,
                    "acknowledged_through_sequence": 7,
                    "next_sequence": 11,
                },
                managers={
                    "network": {"recovery_count": 3},
                    "mqtt": {"recovery_count": 3},
                },
            )
            self.assertTrue(scheduled)
            coordinator._queue.join()
        finally:
            coordinator.stop()

        self.assertEqual(len(repository.pages), 2)
        self.assertEqual(
            [call[2] for call in calls],
            ["get_history", "ack_history", "get_history", "ack_history"],
        )
        self.assertEqual(
            [
                call[3]["through_sequence"]
                for call in calls
                if call[2] == "ack_history"
            ],
            [9, 10],
        )
        status = coordinator.status()
        self.assertEqual(status["completed_runs"], 1)
        self.assertEqual(status["failed_runs"], 0)
        self.assertEqual(status["imported_records"], 3)
        self.assertEqual(status["last_acknowledged"]["atelier"], 10)
        self.assertFalse(
            coordinator.schedule(
                mqtt_device_id="atelier",
                device_uid=DEVICE_UID,
                local_history={
                    "ready": True,
                    "acknowledged_through_sequence": 10,
                    "next_sequence": 12,
                },
                managers={
                    "network": {"recovery_count": 3},
                    "mqtt": {"recovery_count": 3},
                },
            )
        )

    def test_no_backlog_is_not_scheduled(self):
        coordinator = LocalHistoryRecoveryCoordinator(
            command_sender=lambda *args: {},
            repository=FakeRepository(),
        )
        coordinator.start()

        try:
            self.assertFalse(
                coordinator.schedule(
                    mqtt_device_id="atelier",
                    device_uid=DEVICE_UID,
                    local_history={
                        "ready": True,
                        "acknowledged_through_sequence": 10,
                        "next_sequence": 11,
                    },
                    managers={
                        "network": {"recovery_count": 0},
                        "mqtt": {"recovery_count": 0},
                    },
                )
            )

            self.assertFalse(
                coordinator.schedule(
                    mqtt_device_id="atelier",
                    device_uid=DEVICE_UID,
                    local_history={
                        "ready": True,
                        "acknowledged_through_sequence": 10,
                        "next_sequence": 12,
                    },
                    managers={
                        "network": {"recovery_count": 0},
                        "mqtt": {"recovery_count": 0},
                    },
                )
            )
        finally:
            coordinator.stop()


if __name__ == "__main__":
    unittest.main()
