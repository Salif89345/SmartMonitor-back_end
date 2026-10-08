import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.alarm_rule_recovery import AlarmRuleRecoveryCoordinator
from app.device_alarm_configuration import (
    AlarmRuleInput,
    get_alarm_rule_sync_status,
    get_alarm_rules,
    set_alarm_rule,
)
from app.models import Device, DeviceAlarmConfiguration, DeviceMembership, User


def rules():
    return [
        {
            "index": index,
            "id": f"rule_{index}",
            "enabled": False,
            "metric": "temperature_c",
            "direction": "high",
            "severity": "warning",
            "threshold": 40.0,
            "hysteresis": 1.0,
            "delay_ms": 1000,
        }
        for index in range(6)
    ]


class AlarmConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.owner = User(email="owner@example.com", password_hash="test", email_verified=True)
        self.other = User(email="other@example.com", password_hash="test", email_verified=True)
        self.device = Device(device_uid="SM-A1B2C3D4E5F6", mqtt_device_id="atelier")
        self.db.add_all([self.owner, self.other, self.device])
        self.db.flush()
        self.db.add(DeviceMembership(user_id=self.owner.id, device_id=self.device.id, role="owner"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_first_read_imports_and_later_reads_use_backend_copy(self):
        with patch("app.device_alarm_configuration._send_owner_command", return_value=SimpleNamespace(
            result="ack", data={"rules": rules()}
        )) as send:
            first = get_alarm_rules(self.device.id, self.owner, self.db)
            second = get_alarm_rules(self.device.id, self.owner, self.db)
        self.assertEqual(len(first.rules), 6)
        self.assertEqual(len(second.rules), 6)
        send.assert_called_once()
        self.assertEqual(
            self.db.get(DeviceAlarmConfiguration, self.device.id).owner_user_id,
            self.owner.id,
        )

    def test_update_requires_matching_device_ack_before_backend_changes(self):
        self.db.add(DeviceAlarmConfiguration(
            device_id=self.device.id, owner_user_id=self.owner.id, rules=rules()
        ))
        self.db.commit()
        requested = AlarmRuleInput.model_validate({
            **{key: value for key, value in rules()[0].items() if key != "index"},
            "enabled": True,
            "threshold": 42.5,
        })
        echoed = {"index": 0, **requested.model_dump()}
        with patch("app.device_alarm_configuration._send_owner_command", return_value=SimpleNamespace(
            result="ack", data={"rule": echoed}
        )) as send:
            result = set_alarm_rule(self.device.id, 0, requested, self.owner, self.db)
        self.assertTrue(result.rules[0].enabled)
        self.assertEqual(result.rules[0].threshold, 42.5)
        self.assertEqual(send.call_args.kwargs["command"], "set_alarm_rule")
        self.assertEqual(send.call_args.kwargs["parameters"], echoed)

        with patch("app.device_alarm_configuration._send_owner_command", return_value=SimpleNamespace(
            result="nack", data=None
        )):
            with self.assertRaises(HTTPException) as rejected:
                set_alarm_rule(self.device.id, 0, requested.model_copy(update={"threshold": 60.0}), self.owner, self.db)
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(self.db.get(DeviceAlarmConfiguration, self.device.id).rules[0]["threshold"], 42.5)

    def test_non_owner_cannot_read_or_change_rules(self):
        self.db.add(DeviceMembership(user_id=self.other.id, device_id=self.device.id, role="member"))
        self.db.commit()
        with patch("app.device_alarm_configuration._send_owner_command") as send:
            with self.assertRaises(HTTPException) as denied:
                get_alarm_rules(self.device.id, self.other, self.db)
        self.assertEqual(denied.exception.status_code, 403)
        send.assert_not_called()

    def test_stale_previous_owner_configuration_fails_closed(self):
        self.db.add(DeviceAlarmConfiguration(
            device_id=self.device.id, owner_user_id=self.other.id, rules=rules()
        ))
        self.db.commit()
        with patch("app.device_alarm_configuration._send_owner_command") as send:
            with self.assertRaises(HTTPException) as mismatch:
                get_alarm_rules(self.device.id, self.owner, self.db)
        self.assertEqual(mismatch.exception.status_code, 409)
        send.assert_not_called()

    def test_malformed_device_rules_are_rejected_without_persistence(self):
        with patch("app.device_alarm_configuration._send_owner_command", return_value=SimpleNamespace(
            result="ack", data={"rules": rules()[:-1]}
        )):
            with self.assertRaises(HTTPException) as invalid:
                get_alarm_rules(self.device.id, self.owner, self.db)
        self.assertEqual(invalid.exception.status_code, 502)
        self.assertIsNone(self.db.get(DeviceAlarmConfiguration, self.device.id))

    def test_recovery_restores_only_drifted_rules_after_device_reset(self):
        expected = rules()
        expected[2] = {**expected[2], "enabled": True, "threshold": 42.0}
        self.db.add(DeviceAlarmConfiguration(
            device_id=self.device.id, owner_user_id=self.owner.id, rules=expected
        ))
        self.db.commit()
        actual = rules()
        sent = []

        def sender(**kwargs):
            self.assertEqual(kwargs["actor_service"], "alarm_rule_recovery")
            sent.append(kwargs["authorization"].command)
            if sent[-1] == "get_alarm_rules":
                return {"result": "ack", "data": {"rules": list(actual)}}
            rule = kwargs["parameters"]
            actual[rule["index"]] = dict(rule)
            return {"result": "ack", "data": {"rule": dict(rule)}}

        recovery = AlarmRuleRecoveryCoordinator(sender)
        with patch("app.alarm_rule_recovery.SessionLocal", side_effect=lambda: Session(self.engine)):
            self.assertTrue(recovery._reconcile("atelier", self.device.device_uid))
        self.assertEqual(actual, expected)
        self.assertEqual(sent, ["get_alarm_rules", "set_alarm_rule", "get_alarm_rules"])

    def test_recovery_never_reapplies_previous_owners_rules(self):
        self.db.add(DeviceAlarmConfiguration(
            device_id=self.device.id, owner_user_id=self.other.id, rules=rules()
        ))
        self.db.commit()
        sender = unittest.mock.Mock()
        recovery = AlarmRuleRecoveryCoordinator(sender)
        with patch("app.alarm_rule_recovery.SessionLocal", side_effect=lambda: Session(self.engine)):
            self.assertFalse(recovery._reconcile("atelier", self.device.device_uid))
        sender.assert_not_called()

    def test_recovery_rejects_unverified_boot_metadata(self):
        recovery = AlarmRuleRecoveryCoordinator(unittest.mock.Mock())
        recovery.schedule(mqtt_device_id="atelier", device_uid=self.device.device_uid, boot_count=True)
        recovery.schedule(mqtt_device_id="atelier", device_uid="not-an-uid", boot_count=1)
        self.assertTrue(recovery._queue.empty())

    def test_sync_status_checks_live_device_without_overwriting_backend(self):
        self.db.add(DeviceAlarmConfiguration(
            device_id=self.device.id, owner_user_id=self.owner.id, rules=rules()
        ))
        self.db.commit()
        actual = rules()
        actual[4] = {**actual[4], "threshold": 55.0}
        with patch("app.device_alarm_configuration._send_owner_command", return_value=SimpleNamespace(
            result="ack", data={"rules": actual}
        )):
            status = get_alarm_rule_sync_status(self.device.id, self.owner, self.db)
        self.assertFalse(status.synchronized)
        self.assertEqual(status.mismatched_indexes, [4])
        self.assertEqual(self.db.get(DeviceAlarmConfiguration, self.device.id).rules[4]["threshold"], 40.0)
