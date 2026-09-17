import unittest

from fastapi import HTTPException

from app.device_authorization import (
    DeviceAction,
    is_device_action_allowed,
    require_device_action,
)


class DeviceAuthorizationTests(unittest.TestCase):
    def test_owner_can_perform_every_declared_action(self):
        for action in DeviceAction:
            with self.subTest(action=action):
                self.assertTrue(
                    is_device_action_allowed(
                        "owner",
                        action,
                    )
                )

    def test_member_permissions_are_explicit_and_limited(self):
        allowed = {
            DeviceAction.READ_DEVICE,
            DeviceAction.READ_HISTORY,
            DeviceAction.READ_ALARMS,
            DeviceAction.ACKNOWLEDGE_ALARM,
            DeviceAction.READ_EVENTS,
        }

        actual = {
            action
            for action in DeviceAction
            if is_device_action_allowed(
                "member",
                action,
            )
        }

        self.assertEqual(actual, allowed)

    def test_unknown_role_fails_closed(self):
        for action in DeviceAction:
            with self.subTest(action=action):
                self.assertFalse(
                    is_device_action_allowed(
                        "administrator",
                        action,
                    )
                )

    def test_owner_only_action_returns_stable_error(self):
        with self.assertRaises(HTTPException) as raised:
            require_device_action(
                "member",
                DeviceAction.MANAGE_MEMBERS,
            )

        self.assertEqual(
            raised.exception.status_code,
            403,
        )
        self.assertEqual(
            raised.exception.detail,
            "Owner access required",
        )

    def test_member_read_action_is_accepted(self):
        self.assertIsNone(
            require_device_action(
                "member",
                DeviceAction.READ_HISTORY,
            )
        )


if __name__ == "__main__":
    unittest.main()
