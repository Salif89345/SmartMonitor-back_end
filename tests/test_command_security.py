import json
import tempfile
import unittest

from pathlib import Path

from fastapi import HTTPException

from app.command_security import (
    COMMAND_POLICIES,
    LOCAL_HISTORY_RECOVERY_SERVICE,
    AuthorizedDeviceCommand,
    CommandRisk,
    authorize_device_command,
    authorize_service_device_command,
    build_authorized_command_payload,
    expected_confirmation,
)


DEVICE_UID = "SM-A1B2C3D4E5F6"
KEY_HEX = "01" * 32


class CommandPolicyTests(unittest.TestCase):
    def test_every_current_firmware_command_has_a_policy(self):
        current_commands = {
            "ping",
            "get_status",
            "get_config",
            "set_config",
            "get_history",
            "ack_history",
            "get_daily_energy",
            "set_energy_tariff",
            "get_alarm_rules",
            "set_alarm_rule",
            "reset_alarm_rules",
            "ota_update",
            "ota_activate",
        }

        self.assertTrue(current_commands.issubset(COMMAND_POLICIES))

    def test_members_cannot_send_remote_commands(self):
        for command, policy in COMMAND_POLICIES.items():
            confirmation = (
                expected_confirmation(command, DEVICE_UID)
                if policy.confirmation_required
                else None
            )

            with self.subTest(command=command), self.assertRaises(
                HTTPException
            ) as raised:
                authorize_device_command(
                    role="member",
                    device_uid=DEVICE_UID,
                    command=command,
                    confirmation=confirmation,
                )

            self.assertEqual(raised.exception.status_code, 403)

    def test_unknown_command_and_role_fail_closed(self):
        cases = (
            ("owner", "unknown", 400),
            ("administrator", "ping", 403),
        )

        for role, command, expected_status in cases:
            with self.subTest(role=role, command=command), self.assertRaises(
                HTTPException
            ) as raised:
                authorize_device_command(
                    role=role,
                    device_uid=DEVICE_UID,
                    command=command,
                )

            self.assertEqual(
                raised.exception.status_code,
                expected_status,
            )

    def test_critical_commands_require_device_bound_confirmation(self):
        critical_commands = {
            command
            for command, policy in COMMAND_POLICIES.items()
            if policy.risk is CommandRisk.CRITICAL
        }

        self.assertTrue(critical_commands)

        for command in critical_commands:
            with self.subTest(command=command), self.assertRaises(
                HTTPException
            ) as raised:
                authorize_device_command(
                    role="owner",
                    device_uid=DEVICE_UID,
                    command=command,
                    confirmation=f"{command}:SM-000000000000",
                )

            self.assertEqual(raised.exception.status_code, 428)

            decision = authorize_device_command(
                role="owner",
                device_uid=DEVICE_UID,
                command=command,
                confirmation=expected_confirmation(command, DEVICE_UID),
            )
            self.assertIsInstance(decision, AuthorizedDeviceCommand)

    def test_missing_hardware_identity_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            authorize_device_command(
                role="owner",
                device_uid="atelier",
                command="ping",
            )

        self.assertEqual(raised.exception.status_code, 409)

    def test_history_recovery_service_is_narrowly_scoped(self):
        for command in ("get_history", "ack_history"):
            with self.subTest(command=command):
                decision = authorize_service_device_command(
                    service=LOCAL_HISTORY_RECOVERY_SERVICE,
                    device_uid=DEVICE_UID,
                    command=command,
                )
                self.assertEqual(decision.command, command)

        for service, command in (
            (LOCAL_HISTORY_RECOVERY_SERVICE, "ping"),
            ("unknown_service", "get_history"),
        ):
            with self.subTest(service=service, command=command), self.assertRaises(
                PermissionError
            ):
                authorize_service_device_command(
                    service=service,
                    device_uid=DEVICE_UID,
                    command=command,
                )


class CommandEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.keys_path = Path(self.temporary_directory.name) / "keys.json"
        self.keys_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "devices": {
                        DEVICE_UID: {
                            "key_id": "test-key-1",
                            "key_hex": KEY_HEX,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_ping_keeps_the_compatible_plain_contract(self):
        decision = authorize_device_command(
            role="owner",
            device_uid=DEVICE_UID,
            command="ping",
        )
        payload = build_authorized_command_payload(
            authorization=decision,
            request_id="ping-1",
            parameters={},
            keys_path=None,
        )

        self.assertEqual(payload["command"], "ping")

    def test_diagnostic_is_wrapped_and_contains_no_clear_secret(self):
        decision = authorize_device_command(
            role="owner",
            device_uid=DEVICE_UID,
            command="get_status",
        )
        payload = build_authorized_command_payload(
            authorization=decision,
            request_id="status-1",
            parameters={"private_value": "must-stay-inside"},
            keys_path=str(self.keys_path),
            now=1_789_300_000,
            nonce="ab" * 16,
        )
        serialized = json.dumps(payload)

        self.assertEqual(payload["command"], "secure_execute")
        self.assertEqual(payload["parameters"]["device_uid"], DEVICE_UID)
        self.assertEqual(payload["parameters"]["algorithm"], "HMAC-SHA256")
        self.assertNotIn(KEY_HEX, serialized)
        self.assertNotIn("must-stay-inside", serialized)
        self.assertEqual(len(payload["parameters"]["mac"]), 64)


if __name__ == "__main__":
    unittest.main()
