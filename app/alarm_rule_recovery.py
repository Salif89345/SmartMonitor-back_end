"""Reconcile an owned device's offline alarm copy after reconnect or reset.

Only a verified state message schedules work. The service never imports rules
from an ESP over an existing backend record and never creates ownership.
"""

import logging
from queue import Empty, Full, Queue
import threading
import time

from sqlalchemy import select

from app.command_security import (
    ALARM_RULE_RECOVERY_SERVICE,
    authorize_service_device_command,
)
from app.database import SessionLocal
from app.device_identity import is_canonical_device_uid
from app.device_transfer_guard import require_active_transfer_access
from app.models import Device, DeviceAlarmConfiguration, DeviceMembership


logger = logging.getLogger("smartmonitor.alarm_rule_recovery")


class AlarmRuleRecoveryCoordinator:
    def __init__(self, command_sender):
        self._command_sender = command_sender
        self._queue: Queue[tuple[str, str, int] | None] = Queue(maxsize=64)
        self._lock = threading.Lock()
        self._queued: set[tuple[str, str]] = set()
        self._last_attempt: dict[tuple[str, str], float] = {}
        self._verified: dict[tuple[str, str], tuple[int, float]] = {}
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stopping.clear()
            self._worker = threading.Thread(
                target=self._run, name="alarm-rule-recovery", daemon=True
            )
            self._worker.start()

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            worker = self._worker
        if worker is None:
            return
        try:
            self._queue.put_nowait(None)
        except Full:
            # Drop one queued reconciliation so the bounded worker can exit.
            try:
                dropped = self._queue.get_nowait()
                if dropped is not None:
                    with self._lock:
                        self._queued.discard((dropped[0], dropped[1]))
                self._queue.task_done()
                self._queue.put_nowait(None)
            except (Empty, Full):
                logger.warning("Alarm reconciliation queue full during shutdown")
        worker.join(timeout=10)
        with self._lock:
            if not worker.is_alive():
                self._worker = None

    def invalidate(self, mqtt_device_id: str, device_uid: str) -> None:
        with self._lock:
            self._verified.pop((mqtt_device_id, device_uid), None)

    def schedule(self, *, mqtt_device_id: str, device_uid: str, boot_count: int) -> None:
        if (
            not mqtt_device_id
            or not is_canonical_device_uid(device_uid)
            or type(boot_count) is not int
            or boot_count < 1
        ):
            return
        key = (mqtt_device_id, device_uid)
        now = time.monotonic()
        with self._lock:
            verified = self._verified.get(key)
            if verified and verified[0] == boot_count and now - verified[1] < 300:
                return
            if key in self._queued or now - self._last_attempt.get(key, -1e9) < 30:
                return
            self._queued.add(key)
            self._last_attempt[key] = now
        try:
            self._queue.put_nowait((mqtt_device_id, device_uid, boot_count))
        except Full:
            with self._lock:
                self._queued.discard(key)
            logger.warning("Alarm reconciliation queue full")

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                mqtt_device_id, device_uid, boot_count = job
                if self._reconcile(mqtt_device_id, device_uid):
                    with self._lock:
                        self._verified[(mqtt_device_id, device_uid)] = (
                            boot_count, time.monotonic()
                        )
            except Exception:
                logger.exception("Alarm rule reconciliation failed")
            finally:
                if job is not None:
                    with self._lock:
                        self._queued.discard((job[0], job[1]))
                self._queue.task_done()

    def _reconcile(self, mqtt_device_id: str, device_uid: str) -> bool:
        # Import at execution time to avoid a circular import with commands.
        from app.device_alarm_configuration import _same_rule, _validated_rules

        with SessionLocal() as db:
            if self._stopping.is_set():
                return False
            device = db.scalar(select(Device).where(
                Device.mqtt_device_id == mqtt_device_id,
                Device.device_uid == device_uid,
            ))
            if device is None or not device.is_active:
                return True
            configuration = db.get(DeviceAlarmConfiguration, device.id)
            if configuration is None:
                return True  # First import still requires the owner's explicit read.
            owner_id = configuration.owner_user_id
            role = db.scalar(select(DeviceMembership.role).where(
                DeviceMembership.device_id == device.id,
                DeviceMembership.user_id == owner_id,
            ))
            if role != "owner":
                return False
            require_active_transfer_access(
                db, device_id=device.id, user_id=owner_id, role=role
            )
            expected = _validated_rules(configuration.rules)
            response = self._send(
                mqtt_device_id, device_uid, "get_alarm_rules", {}
            )
            if response.get("result") != "ack" or not isinstance(response.get("data"), dict):
                return False
            actual = _validated_rules(response["data"].get("rules"))
            if all(_same_rule(actual[i], expected[i]) for i in range(6)):
                return True

            for index in range(6):
                if self._stopping.is_set():
                    return False
                if _same_rule(actual[index], expected[index]):
                    continue
                # Ownership and intended rules may change while MQTT is in flight.
                db.expire_all()
                current = db.get(DeviceAlarmConfiguration, device.id)
                current_device = db.get(Device, device.id)
                role = db.scalar(select(DeviceMembership.role).where(
                    DeviceMembership.device_id == device.id,
                    DeviceMembership.user_id == owner_id,
                ))
                if (
                    current is None
                    or current_device is None
                    or not current_device.is_active
                    or current_device.device_uid != device_uid
                    or current_device.mqtt_device_id != mqtt_device_id
                    or current.owner_user_id != owner_id
                    or role != "owner"
                    or _validated_rules(current.rules) != expected
                ):
                    return False
                require_active_transfer_access(
                    db, device_id=device.id, user_id=owner_id, role=role
                )
                reply = self._send(
                    mqtt_device_id, device_uid, "set_alarm_rule", expected[index]
                )
                if reply.get("result") != "ack" or not isinstance(reply.get("data"), dict):
                    return False
                echoed = _validated_rules([
                    *(rule for rule in expected if rule["index"] != index),
                    reply["data"].get("rule"),
                ])[index]
                if not _same_rule(echoed, expected[index]):
                    return False

            final = self._send(mqtt_device_id, device_uid, "get_alarm_rules", {})
            if final.get("result") != "ack" or not isinstance(final.get("data"), dict):
                return False
            confirmed = _validated_rules(final["data"].get("rules"))
            return all(_same_rule(confirmed[i], expected[i]) for i in range(6))

    def _send(self, mqtt_device_id, device_uid, command, parameters):
        if self._stopping.is_set():
            raise RuntimeError("Alarm reconciliation is stopping")
        authorization = authorize_service_device_command(
            service=ALARM_RULE_RECOVERY_SERVICE,
            device_uid=device_uid,
            command=command,
        )
        return self._command_sender(
            mqtt_device_id=mqtt_device_id,
            authorization=authorization,
            actor_user_id=None,
            actor_service=ALARM_RULE_RECOVERY_SERVICE,
            parameters=parameters,
            timeout=8.0,
        )
