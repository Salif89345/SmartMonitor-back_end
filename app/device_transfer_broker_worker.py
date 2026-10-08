"""Broker-gated SM-015 transfer completion, deliberately not scheduled yet.

Only a server-owned adapter backed by an authenticated broker control plane may
implement ``attest`` and ``verify``. An MQTT message, client claim, screenshot,
or database receipt is not an attestation. The current EMQX Serverless plan has
no suitable published control-plane API, so no production adapter is wired.
"""

from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.device_transfer_finalizer import (
    RetainedMqttIdentityVerifier,
    complete_due_transfer,
)
from app.device_transfer_store import TransferRejected
from app.models import Device, DeviceTransfer, DeviceTransferMqttIdentityCheck
from app.settings import SM015_TRANSFER_STAGING_ENABLED


class BrokerIdentityAttestor(RetainedMqttIdentityVerifier, Protocol):
    """Control-plane adapter checking credential uniqueness and scoped ACLs.

    ``attest`` must return a non-reusable audit reference only after checking
    the retained per-device credential and both publish and subscribe ACLs.
    ``verify`` must re-check the same facts live for that exact reference.
    """

    def attest(self, *, device_uid: str, mqtt_device_id: str) -> str | None: ...


def complete_due_transfer_from_broker(
    db: Session,
    *,
    transfer_id: int,
    attestor: BrokerIdentityAttestor | None,
    now: datetime | None = None,
) -> DeviceTransfer:
    """Fetch fresh broker evidence and atomically switch ownership if valid.

    This is an internal worker entry point, not an API route. The receipt and
    ownership switch share a transaction; a rejection cannot leave a forged or
    partial receipt behind. A completed transfer is safe to retry.
    """
    if not SM015_TRANSFER_STAGING_ENABLED:
        raise TransferRejected("ownership transfer is disabled")
    if attestor is None:
        raise TransferRejected("trusted broker attestor required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)

    try:
        transfer = db.get(DeviceTransfer, transfer_id)
        if transfer is None:
            raise TransferRejected("transfer not found")
        if transfer.state == "completed":
            return complete_due_transfer(
                db, transfer_id=transfer_id, identity_verifier=attestor, now=current
            )
        if transfer.state != "pending":
            raise TransferRejected("transfer not pending")
        deadline = transfer.not_before
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if current < deadline.astimezone(timezone.utc):
            raise TransferRejected("transfer delay not elapsed")

        device = db.get(Device, transfer.device_id)
        if device is None or not device.mqtt_device_id:
            raise TransferRejected("device-scoped MQTT identity required")
        attested_uid = device.device_uid
        attested_mqtt_id = device.mqtt_device_id
        reference = attestor.attest(
            device_uid=attested_uid, mqtt_device_id=attested_mqtt_id
        )
        if (
            not isinstance(reference, str)
            or not reference.strip()
            or reference != reference.strip()
            or len(reference) > 128
            or "\r" in reference
            or "\n" in reference
        ):
            raise TransferRejected("trusted broker attestation required")

        # The broker call was made without holding database row locks. Recheck
        # the same device and request under the finalizer's lock order before
        # storing evidence, so a concurrent change cannot redirect the proof.
        locked_device = db.scalar(
            select(Device).where(Device.id == device.id)
            .with_for_update().execution_options(populate_existing=True)
        )
        locked_transfer = db.scalar(
            select(DeviceTransfer)
            .where(DeviceTransfer.id == transfer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            locked_device is None
            or locked_transfer is None
            or locked_transfer.state != "pending"
            or locked_transfer.device_id != device.id
            or locked_device.device_uid != attested_uid
            or locked_device.mqtt_device_id != attested_mqtt_id
        ):
            raise TransferRejected("transfer or broker identity changed")

        receipt = db.get(DeviceTransferMqttIdentityCheck, transfer_id)
        if receipt is None:
            receipt = DeviceTransferMqttIdentityCheck(transfer_id=transfer_id)
            db.add(receipt)
        receipt.device_uid = device.device_uid
        receipt.mqtt_device_id = device.mqtt_device_id
        receipt.broker_reference = reference
        receipt.verified_at = current
        db.flush()
        return complete_due_transfer(
            db, transfer_id=transfer_id, identity_verifier=attestor, now=current
        )
    except Exception:
        db.rollback()
        raise


def due_transfer_ids(db: Session, *, now: datetime | None = None, limit: int = 100) -> list[int]:
    """Find eligible requests for a future scheduler; this does not mutate them."""
    if not SM015_TRANSFER_STAGING_ENABLED:
        return []
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    return list(db.scalars(
        select(DeviceTransfer.id)
        .where(DeviceTransfer.state == "pending", DeviceTransfer.not_before <= current)
        .order_by(DeviceTransfer.not_before, DeviceTransfer.id)
        .limit(limit)
    ))
