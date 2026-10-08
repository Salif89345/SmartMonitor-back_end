from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

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
            device_uid="SM-A1B2C3D4E5F6",
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
        confirmation=None,
    ):
        rate_limit = Mock()

        with patch.object(
            commands,
            "enforce_command_rate_limit",
            rate_limit,
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
                confirmation=confirmation,
            )

        return result, send_command, rate_limit

    def test_owner_command_uses_identity_and_audited_authorization(self):
        result, send_command, rate_limit = self._send()
        call = send_command.call_args.kwargs

        self.assertEqual(call["mqtt_device_id"], "atelier")
        self.assertEqual(call["authorization"].command, "ping")
        self.assertEqual(
            call["authorization"].device_uid,
            "SM-A1B2C3D4E5F6",
        )
        self.assertEqual(call["actor_user_id"], 32)
        self.assertEqual(call["parameters"], {})
        self.assertEqual(call["timeout"], 5.0)
        rate_limit.assert_called_once_with(32)
        self.assertEqual(result.request_id, "request-123")

    def test_nack_is_a_valid_device_response(self):
        self.response.update(
            result="nack",
            error_code="device_busy",
        )

        result, _, _ = self._send(command="get_status")

        self.assertEqual(result.result, "nack")
        self.assertEqual(result.error_code, "device_busy")

    def test_restart_requires_device_bound_confirmation(self):
        with self.assertRaises(HTTPException) as missing:
            self._send(command="restart")
        self.assertEqual(missing.exception.status_code, 428)

        with self.assertRaises(HTTPException) as wrong:
            self._send(
                command="restart",
                confirmation="restart:SM-000000000000",
            )
        self.assertEqual(wrong.exception.status_code, 428)

        _, sent, _ = self._send(
            command="restart",
            confirmation="restart:SM-A1B2C3D4E5F6",
        )
        self.assertEqual(
            sent.call_args.kwargs["authorization"].command,
            "restart",
        )

    def test_member_cannot_restart_with_valid_confirmation(self):
        with self.assertRaises(HTTPException) as denied:
            self._send(
                command="restart",
                row=(self.device, "member"),
                confirmation="restart:SM-A1B2C3D4E5F6",
            )
        self.assertEqual(denied.exception.status_code, 403)

    def test_restart_route_passes_confirmation_to_owner_guard(self):
        request = commands.RestartCommandRequest(
            confirmation="restart:SM-A1B2C3D4E5F6",
        )
        with patch.object(
            commands,
            "_send_owner_command",
            return_value=self.response,
        ) as guarded_send:
            commands.restart_device(
                device_id=7,
                request=request,
                current_user=self.user,
                db=_Database((self.device, "owner")),
            )
        self.assertEqual(
            guarded_send.call_args.kwargs["confirmation"],
            request.confirmation,
        )
        self.assertEqual(
            guarded_send.call_args.kwargs["command"],
            "restart",
        )

    def test_get_config_exposes_only_non_secret_fields(self):
        self.response["data"] = {
            "device": "atelier",
            "schema_version": 1,
            "wifi_password": "must-not-leak",
            "mqtt_password": "must-not-leak",
        }
        with patch.object(
            commands.mqtt_manager,
            "send_command",
            return_value=self.response,
        ):
            response = commands.get_config_device(
                device_id=7,
                current_user=self.user,
                db=_Database((self.device, "owner")),
            )
        self.assertEqual(response.data, {"device": "atelier", "schema_version": 1})
        self.assertEqual(response.message, "Configuration récupérée")

    def test_get_config_refuses_member_before_publish(self):
        with patch.object(commands.mqtt_manager, "send_command") as publish:
            with self.assertRaises(HTTPException) as raised:
                commands.get_config_device(
                    device_id=7,
                    current_user=self.user,
                    db=_Database((self.device, "member")),
                )
        self.assertEqual(raised.exception.status_code, 403)
        publish.assert_not_called()

    def test_get_config_rejects_malformed_ack(self):
        self.response["data"] = {"mqtt_password": "must-not-leak"}
        with patch.object(
            commands.mqtt_manager,
            "send_command",
            return_value=self.response,
        ):
            with self.assertRaises(HTTPException) as raised:
                commands.get_config_device(
                    device_id=7,
                    current_user=self.user,
                    db=_Database((self.device, "owner")),
                )
        self.assertEqual(raised.exception.status_code, 502)

    def test_get_config_nack_does_not_expose_device_text(self):
        self.response.update(
            result="nack",
            message="secret from device",
            error_code="invalid_parameters",
            data={"wifi_password": "must-not-leak"},
        )
        with patch.object(
            commands.mqtt_manager,
            "send_command",
            return_value=self.response,
        ):
            response = commands.get_config_device(
                device_id=7,
                current_user=self.user,
                db=_Database((self.device, "owner")),
            )
        self.assertEqual(response.result, "nack")
        self.assertEqual(response.error_code, "invalid_parameters")
        self.assertEqual(response.message, "Lecture de configuration refusée")
        self.assertIsNone(response.data)

    def test_member_is_refused_before_rate_limit_and_publish(self):
        rate_limit = Mock()

        with patch.object(
            commands,
            "enforce_command_rate_limit",
            rate_limit,
        ), patch.object(
            commands.mqtt_manager,
            "send_command",
        ) as send_command, self.assertRaises(HTTPException) as raised:
            commands._send_owner_command(
                device_id=7,
                command="ping",
                current_user=self.user,
                db=_Database((self.device, "member")),
            )

        self.assertEqual(raised.exception.status_code, 403)
        rate_limit.assert_not_called()
        send_command.assert_not_called()

    def test_unassociated_device_is_refused(self):
        with self.assertRaises(HTTPException) as raised:
            self._send(row=None)

        self.assertEqual(raised.exception.status_code, 404)

    def test_missing_hardware_identity_is_refused(self):
        self.device.device_uid = "atelier"

        with self.assertRaises(HTTPException) as raised:
            self._send()

        self.assertEqual(raised.exception.status_code, 409)

    def test_missing_mqtt_identity_is_refused(self):
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
