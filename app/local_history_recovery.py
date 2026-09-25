"""Automatic, crash-safe recovery of the ESP32 circular history buffer."""

from __future__ import annotations

import json
import logging
import math
import threading
import time

from dataclasses import dataclass
from datetime import date, datetime, timezone
from queue import Empty, Full, Queue
from typing import Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from app.database import SessionLocal
from app.measurement_contract import (
    ELECTRICAL_FIELD_QUALITY_KEYS,
    normalize_electrical_measurement,
)
from app.models import (
    Device,
    DeviceChannel,
    LocalHistoryRecord,
    PowerMeasurement,
)
from app.power_daily_summary import (
    local_date_for_timestamp,
    rebuild_completed_day_summary,
)
from app.settings import SM015_TRANSFER_STAGING_ENABLED


logger = logging.getLogger("smartmonitor.history_recovery")

HISTORY_PAGE_LIMIT = 2
HISTORY_MAX_PAGES_PER_RUN = 256
HISTORY_RETRY_DELAY_SECONDS = 30
POWER_CHANNEL_KEYS = (
    "power_1",
    "power_2",
    "power_3",
    "power_4",
)


class HistoryRecoveryError(RuntimeError):
    """Raised when a page cannot be safely persisted or acknowledged."""


@dataclass(frozen=True)
class HistoryRecoveryRequest:
    mqtt_device_id: str
    device_uid: str
    acknowledged_through_sequence: int
    newest_sequence: int
    recovery_marker: tuple[int, int]


@dataclass(frozen=True)
class HistoryPage:
    records: tuple[dict, ...]
    next_cursor: int
    has_more: bool


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise HistoryRecoveryError(
            f"{field_name} must be a positive integer"
        )

    return value


def parse_history_page(
    response: dict,
    *,
    after_sequence: int,
) -> HistoryPage:
    """Validate a firmware page before any database or ACK operation."""

    if not isinstance(response, dict) or response.get("result") != "ack":
        raise HistoryRecoveryError("get_history was not acknowledged")

    data = response.get("data")

    if not isinstance(data, dict):
        raise HistoryRecoveryError("get_history data is missing")

    records = data.get("records")
    returned_count = data.get("returned_count")
    next_cursor = data.get("next_cursor")
    has_more = data.get("has_more")

    if not isinstance(records, list):
        raise HistoryRecoveryError("history records must be a list")

    if len(records) > HISTORY_PAGE_LIMIT:
        raise HistoryRecoveryError("history page exceeds the MQTT budget")

    if type(returned_count) is not int or returned_count != len(records):
        raise HistoryRecoveryError("history returned_count is inconsistent")

    if type(next_cursor) is not int or next_cursor < after_sequence:
        raise HistoryRecoveryError("history next_cursor is invalid")

    if type(has_more) is not bool:
        raise HistoryRecoveryError("history has_more is invalid")

    previous_sequence = after_sequence

    for record in records:
        if not isinstance(record, dict):
            raise HistoryRecoveryError("history record must be an object")

        sequence = _positive_int(record.get("sequence"), "sequence")

        if sequence <= previous_sequence:
            raise HistoryRecoveryError("history sequences are not ordered")

        _positive_int(record.get("captured_at"), "captured_at")
        previous_sequence = sequence

    if records and next_cursor != previous_sequence:
        raise HistoryRecoveryError("history cursor does not match the page")

    if not records and next_cursor != after_sequence:
        raise HistoryRecoveryError("empty history page advanced the cursor")

    if has_more and not records:
        raise HistoryRecoveryError("empty history page cannot have more data")

    return HistoryPage(
        records=tuple(records),
        next_cursor=next_cursor,
        has_more=has_more,
    )


def validate_history_ack(response: dict, expected_sequence: int) -> None:
    if not isinstance(response, dict) or response.get("result") != "ack":
        raise HistoryRecoveryError("ack_history was not acknowledged")

    data = response.get("data")

    if not isinstance(data, dict):
        raise HistoryRecoveryError("ack_history data is missing")

    if data.get("through_sequence") != expected_sequence:
        raise HistoryRecoveryError("ack_history confirmed another sequence")


def _normalize_history_energy(values: object) -> dict[str, float | None]:
    if not isinstance(values, dict):
        raise HistoryRecoveryError("energy channel must be an object")

    contract_input: dict[str, object] = {"freshness": "fresh"}

    for field_name, quality_name in ELECTRICAL_FIELD_QUALITY_KEYS.items():
        value = values.get(field_name)

        if value is None:
            contract_input[field_name] = None
            contract_input[quality_name] = "unavailable"
            continue

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise HistoryRecoveryError(
                f"invalid historical value: {field_name}"
            )

        contract_input[field_name] = float(value)
        contract_input[quality_name] = "ok"

    normalized, reason = normalize_electrical_measurement(contract_input)

    if normalized is None:
        raise HistoryRecoveryError(
            "historical energy contract rejected: " + str(reason)
        )

    return normalized


class LocalHistoryRepository:
    """Persist complete raw records and normalized power measurements."""

    def persist_page(
        self,
        *,
        mqtt_device_id: str,
        device_uid: str,
        records: tuple[dict, ...],
    ) -> int:
        if not records:
            return 0

        # Replayed records need a separate, epoch-aware attribution policy.
        # Until then, never import an unowned measurement in transfer mode.
        if SM015_TRANSFER_STAGING_ENABLED:
            raise HistoryRecoveryError(
                "historical recovery unavailable during ownership-transfer staging"
            )

        db = SessionLocal()

        try:
            device = db.scalar(
                select(Device).where(
                    Device.mqtt_device_id == mqtt_device_id,
                    Device.device_uid == device_uid,
                    Device.is_active.is_(True),
                )
            )

            if device is None:
                raise HistoryRecoveryError(
                    "history device identity is unknown"
                )

            channel_rows = list(
                db.scalars(
                    select(DeviceChannel).where(
                        DeviceChannel.device_id == device.id,
                        DeviceChannel.channel_key.in_(POWER_CHANNEL_KEYS),
                    )
                ).all()
            )
            channels = {
                channel.channel_key: channel.id
                for channel in channel_rows
            }
            imported_count = 0
            affected_completed_days: set[tuple[int, date]] = set()
            current_local_date = local_date_for_timestamp(
                datetime.now(timezone.utc)
            )

            for record in records:
                sequence = _positive_int(record.get("sequence"), "sequence")
                captured_epoch = _positive_int(
                    record.get("captured_at"),
                    "captured_at",
                )

                try:
                    captured_at = datetime.fromtimestamp(
                        captured_epoch,
                        tz=timezone.utc,
                    )
                except (OverflowError, OSError, ValueError) as error:
                    raise HistoryRecoveryError(
                        "historical timestamp is out of range"
                    ) from error

                try:
                    immutable_record = json.loads(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                    )
                except (TypeError, ValueError) as error:
                    raise HistoryRecoveryError(
                        "historical record is not valid JSON"
                    ) from error

                energy_channels = record.get("energy_channels", {})

                if not isinstance(energy_channels, dict):
                    raise HistoryRecoveryError(
                        "energy_channels must be an object"
                    )

                prepared_channels: list[tuple[int, dict[str, float | None]]] = []

                for channel_key, energy_values in energy_channels.items():
                    if channel_key not in POWER_CHANNEL_KEYS:
                        raise HistoryRecoveryError(
                            "unknown historical channel"
                        )

                    channel_id = channels.get(channel_key)

                    if channel_id is None:
                        raise HistoryRecoveryError(
                            f"historical channel is not configured: {channel_key}"
                        )

                    prepared_channels.append(
                        (
                            channel_id,
                            _normalize_history_energy(energy_values),
                        )
                    )

                ledger_statement = (
                    postgresql_insert(LocalHistoryRecord)
                    .values(
                        device_id=device.id,
                        sequence=sequence,
                        captured_at=captured_at,
                        record=immutable_record,
                    )
                    .on_conflict_do_nothing(
                        constraint=(
                            "uq_local_history_records_device_sequence"
                        )
                    )
                    .returning(LocalHistoryRecord.id)
                )
                ledger_id = db.scalar(ledger_statement)

                if ledger_id is None:
                    continue

                imported_count += 1

                for channel_id, normalized in prepared_channels:
                    measurement_statement = (
                        postgresql_insert(PowerMeasurement)
                        .values(
                            channel_id=channel_id,
                            measured_at=captured_at,
                            voltage_v=normalized["voltage_v"],
                            current_a=normalized["current_a"],
                            power_w=normalized["power_w"],
                            energy_kwh=normalized["energy_kwh"],
                            frequency_hz=normalized["frequency_hz"],
                            power_factor=normalized["power_factor"],
                        )
                        .on_conflict_do_nothing(
                            constraint=(
                                "uq_power_measurements_channel_measured_at"
                            )
                        )
                    )
                    db.execute(measurement_statement)

                    captured_local_date = local_date_for_timestamp(
                        captured_at
                    )
                    if captured_local_date < current_local_date:
                        affected_completed_days.add(
                            (channel_id, captured_local_date)
                        )

            for channel_id, summary_date in affected_completed_days:
                rebuild_completed_day_summary(
                    db,
                    channel_id=channel_id,
                    summary_date=summary_date,
                    current_local_date=current_local_date,
                )

            db.commit()
            return imported_count

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


class LocalHistoryRecoveryCoordinator:
    """Serialize one recovery per device outside the MQTT callback threads."""

    def __init__(
        self,
        *,
        command_sender: Callable[[str, str, str, dict], dict],
        repository: LocalHistoryRepository | None = None,
    ) -> None:
        self._command_sender = command_sender
        self._repository = repository or LocalHistoryRepository()
        self._queue: Queue[HistoryRecoveryRequest | None] = Queue(maxsize=100)
        self._lock = threading.Lock()
        self._pending_devices: set[str] = set()
        self._retry_after: dict[str, float] = {}
        self._completed_markers: dict[str, tuple[int, int]] = {}
        self._worker: threading.Thread | None = None
        self._running = False
        self._completed_runs = 0
        self._failed_runs = 0
        self._imported_records = 0
        self._last_acknowledged: dict[str, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return

            self._running = True
            self._worker = threading.Thread(
                target=self._run,
                name="smartmonitor-history-recovery",
                daemon=True,
            )
            self._worker.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            worker = self._worker

        if worker is None or not worker.is_alive():
            return

        try:
            self._queue.put(None, timeout=2)
        except Full:
            return

        worker.join(timeout=10)

    def schedule(
        self,
        *,
        mqtt_device_id: str,
        device_uid: str,
        local_history: object,
        managers: object,
    ) -> bool:
        if not mqtt_device_id or not isinstance(device_uid, str):
            return False

        if not isinstance(local_history, dict):
            return False

        if not isinstance(managers, dict):
            return False

        network = managers.get("network")
        mqtt = managers.get("mqtt")

        if not isinstance(network, dict) or not isinstance(mqtt, dict):
            return False

        network_recovery = network.get("recovery_count")
        mqtt_recovery = mqtt.get("recovery_count")

        if (
            type(network_recovery) is not int
            or network_recovery < 0
            or type(mqtt_recovery) is not int
            or mqtt_recovery < 0
        ):
            return False

        recovery_marker = (network_recovery, mqtt_recovery)

        if local_history.get("ready") is not True:
            return False

        acknowledged = local_history.get("acknowledged_through_sequence")
        next_sequence = local_history.get("next_sequence")

        if (
            type(acknowledged) is not int
            or acknowledged < 0
            or type(next_sequence) is not int
            or next_sequence < 1
        ):
            return False

        newest_sequence = next_sequence - 1

        now = time.monotonic()

        with self._lock:
            if not self._running:
                return False

            if mqtt_device_id in self._pending_devices:
                return False

            if self._completed_markers.get(mqtt_device_id) == recovery_marker:
                return False

            if newest_sequence <= acknowledged:
                self._completed_markers[mqtt_device_id] = recovery_marker
                return False

            if now < self._retry_after.get(mqtt_device_id, 0.0):
                return False

            self._pending_devices.add(mqtt_device_id)

        request = HistoryRecoveryRequest(
            mqtt_device_id=mqtt_device_id,
            device_uid=device_uid,
            acknowledged_through_sequence=acknowledged,
            newest_sequence=newest_sequence,
            recovery_marker=recovery_marker,
        )

        try:
            self._queue.put_nowait(request)
        except Full:
            with self._lock:
                self._pending_devices.discard(mqtt_device_id)
            return False

        return True

    def _run(self) -> None:
        while True:
            try:
                request = self._queue.get(timeout=1)
            except Empty:
                with self._lock:
                    if not self._running:
                        return
                continue

            try:
                if request is None:
                    return

                self._recover(request)

            except Exception as error:
                if request is not None:
                    with self._lock:
                        self._failed_runs += 1
                        self._retry_after[request.mqtt_device_id] = (
                            time.monotonic() + HISTORY_RETRY_DELAY_SECONDS
                        )

                    logger.error(
                        "[SM-049] Local history recovery failed"
                        " | device: %s | error: %s",
                        request.mqtt_device_id,
                        type(error).__name__,
                    )

            finally:
                if request is not None:
                    with self._lock:
                        self._pending_devices.discard(
                            request.mqtt_device_id
                        )
                self._queue.task_done()

    def _recover(self, request: HistoryRecoveryRequest) -> None:
        cursor = request.acknowledged_through_sequence
        imported_total = 0

        for _ in range(HISTORY_MAX_PAGES_PER_RUN):
            response = self._command_sender(
                request.mqtt_device_id,
                request.device_uid,
                "get_history",
                {
                    "from_epoch": 0,
                    "to_epoch": 0,
                    "after_sequence": cursor,
                    "limit": HISTORY_PAGE_LIMIT,
                },
            )
            page = parse_history_page(
                response,
                after_sequence=cursor,
            )

            if not page.records:
                break

            imported_total += self._repository.persist_page(
                mqtt_device_id=request.mqtt_device_id,
                device_uid=request.device_uid,
                records=page.records,
            )

            ack_response = self._command_sender(
                request.mqtt_device_id,
                request.device_uid,
                "ack_history",
                {"through_sequence": page.next_cursor},
            )
            validate_history_ack(
                ack_response,
                page.next_cursor,
            )
            cursor = page.next_cursor

            if not page.has_more:
                break
        else:
            raise HistoryRecoveryError(
                "history recovery exceeded the bounded page count"
            )

        with self._lock:
            self._completed_runs += 1
            self._imported_records += imported_total
            self._last_acknowledged[request.mqtt_device_id] = cursor
            self._completed_markers[
                request.mqtt_device_id
            ] = request.recovery_marker
            self._retry_after.pop(request.mqtt_device_id, None)

        logger.info(
            "[SM-049] Local history recovery complete"
            " | device: %s | imported: %s | acknowledged_through: %s",
            request.mqtt_device_id,
            imported_total,
            cursor,
        )

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "queue_depth": self._queue.qsize(),
                "pending_devices": len(self._pending_devices),
                "completed_runs": self._completed_runs,
                "failed_runs": self._failed_runs,
                "imported_records": self._imported_records,
                "last_acknowledged": dict(self._last_acknowledged),
            }
