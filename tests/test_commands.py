from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from app import commands
from app.mqtt_client import DeviceUnavailableError


_DEFAULT_ROW = object()


class _Database:
    def __init__(self, row):
        self.row = row

    def execute(self, statement):
        return self

    def first(self):
        return self.row


class CommandRouteTests(TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=32)
        self.device = SimpleNamespace(
            id=7,
            mqtt_device_id="atelier",
        )
        self.response = {
            "request_id": "request-123",
            "result": "ack",
            "error_code": None,
            "message": "pong",
            "data": {"reply": "pong"},
        }

    def _send(
        self,
        command="ping",
        row=_DEFAULT_ROW,
        error=None,
    ):
        with patch.object(
            commands,
            "enforce_command_rate_limit",
        ), patch.object(
            commands.mqtt_manager,
            "send_command",
            return_value=self.response,
            side_effect=error,
        ) as send_command:
            result = commands._send_owner_command(
                device_id=7,
                command=command,
                current_user=self.user,
                db=_Database(
                    (self.device, "owner")
                    if row is _DEFAULT_ROW
                    else row
                ),
            )

        return result, send_command

    def test_owner_command_uses_linked_mqtt_identity(self):
        result, send_command = self._send()

        send_command.assert_called_once_with(
            mqtt_device_id="atelier",
            command="ping",
            parameters={},
            timeout=5.0,
        )
        self.assertEqual(result.request_id, "request-123")
        self.assertEqual(result.result, "ack")

    def test_nack_is_a_valid_device_response(self):
        self.response.update(
            result="nack",
            error_code="device_busy",
        )

        result, _ = self._send(command="get_status")

        self.assertEqual(result.result, "nack")
        self.assertEqual(result.error_code, "device_busy")

    def test_member_and_unassociated_device_are_refused(self):
        for row, expected_status in (
            ((self.device, "member"), 403),
            (None, 404),
        ):
            with self.subTest(expected_status=expected_status), self.assertRaises(
                HTTPException
            ) as raised:
                self._send(row=row)

            self.assertEqual(
                raised.exception.status_code,
                expected_status,
            )

    def test_missing_identity_is_refused(self):
        self.device.mqtt_device_id = None

        with self.assertRaises(HTTPException) as raised:
            self._send()

        self.assertEqual(raised.exception.status_code, 409)

    def test_transport_errors_keep_the_http_contract(self):
        cases = (
            (DeviceUnavailableError("offline"), 503),
            (TimeoutError(), 504),
            (ValueError(), 502),
            (RuntimeError(), 503),
        )

        for error, expected_status in cases:
            with self.subTest(error=type(error).__name__), self.assertRaises(
                HTTPException
            ) as raised:
                self._send(error=error)

            self.assertEqual(
                raised.exception.status_code,
                expected_status,
            )
