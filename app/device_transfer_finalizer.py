"""Atomic ownership switch after a trusted retained-MQTT-identity check.

There is deliberately no public route or scheduler here. The current shared
prototype MQTT account cannot produce the required per-device broker receipt.
"""

from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.device_transfer_notice import (
    TRANSFER_CAUSE,
    completed_transfer_notice,
    new_owner_transfer_notice,
)
from app.device_transfer_store import TransferRejected
from app.models import (
    Device,
    DeviceAlarmConfiguration,
    DeviceChannel,
    DeviceMembership,
    DeviceTransfer,
    DeviceTransferMqttIdentityCheck,
    DeviceTransferNotice,
    User,
)
from app.settings import SM015_TRANSFER_STAGING_ENABLED


MAX_IDENTITY_CHECK_AGE = timedelta(minutes=2)


class RetainedMqttIdentityVerifier(Protocol):
    """Trusted adapter that checks broker-side auth and device-scoped ACLs.

    Its answer must come from an authenticated broker control plane, never
    from an MQTT payload or a client-supplied declaration. No production
    adapter exists yet, so the default finalization path fails closed.
    """

    def verify(
        self,
        *,
        device_uid: str,
        mqtt_device_id: str,
        broker_reference: str,
    ) -> bool: ...


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite test storage loses tzinfo; PostgreSQL does not.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def complete_due_transfer(
    db: Session,
    *,
    transfer_id: int,
    identity_verifier: RetainedMqttIdentityVerifier | None = None,
    now: datetime | None = None,
) -> DeviceTransfer:
    """Switch rights once, only after the deadline and broker identity check.

    A trusted broker worker must verify that the retained credential is unique
    to this device and that its topic ACL is device-scoped. The stored check
    is only an audit receipt, not proof by itself. No production verifier is
    enabled yet. A failed commit leaves the request pending for a safe retry.
    """
    if not SM015_TRANSFER_STAGING_ENABLED:
        raise TransferRejected("ownership transfer is disabled")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)

    try:
        device_id = db.scalar(
            select(DeviceTransfer.device_id).where(DeviceTransfer.id == transfer_id)
        )
        if device_id is None:
            raise TransferRejected("transfer not found")

        # Use the same lock order as live measurement attribution.
        device = db.scalar(select(Device).where(Device.id == device_id).with_for_update())
        transfer = db.scalar(
            select(DeviceTransfer)
            .where(DeviceTransfer.id == transfer_id)
            .with_for_update()
        )
        if device is None or transfer is None or transfer.device_id != device.id:
            raise TransferRejected("transfer device missing")
        if transfer.state == "completed":
            current_memberships = list(
                db.scalars(
                    select(DeviceMembership).where(DeviceMembership.device_id == device.id)
                ).all()
            )
            current_owners = [
                member for member in current_memberships if member.role == "owner"
            ]
            if (
                transfer.effective_at is None
                or len(current_owners) != 1
                or current_owners[0].user_id != transfer.next_owner_user_id
                or any(
                    member.user_id == transfer.previous_owner_user_id
                    for member in current_memberships
                )
            ):
                raise TransferRejected("completed transfer is inconsistent")
            db.commit()
            return transfer
        if transfer.state != "pending" or transfer.cause != TRANSFER_CAUSE:
            raise TransferRejected("transfer not pending")
        if _utc(transfer.requested_at) >= _utc(transfer.not_before):
            raise TransferRejected("transfer deadline is inconsistent")
        if current < _utc(transfer.not_before):
            raise TransferRejected("transfer delay not elapsed")

        identity_check = db.get(DeviceTransferMqttIdentityCheck, transfer.id)
        if (
            identity_check is None
            or not device.mqtt_device_id
            or identity_check.device_uid != device.device_uid
            or identity_check.mqtt_device_id != device.mqtt_device_id
            or not identity_check.broker_reference.strip()
            or _utc(identity_check.verified_at) < _utc(transfer.requested_at)
            or _utc(identity_check.verified_at) > current
            or current - _utc(identity_check.verified_at) > MAX_IDENTITY_CHECK_AGE
        ):
            raise TransferRejected("verified device-scoped MQTT identity required")
        if identity_verifier is None:
            raise TransferRejected("live broker identity verification required")
        verified = identity_verifier.verify(
            device_uid=device.device_uid,
            mqtt_device_id=device.mqtt_device_id,
            broker_reference=identity_check.broker_reference,
        )
        if verified is not True:
            raise TransferRejected("live broker identity verification required")

        memberships = list(
            db.scalars(
                select(DeviceMembership).where(DeviceMembership.device_id == device.id)
            ).all()
        )
        owners = [member for member in memberships if member.role == "owner"]
        if len(owners) != 1 or owners[0].user_id != transfer.previous_owner_user_id:
            raise TransferRejected("previous owner is inconsistent")
        next_owner = db.get(User, transfer.next_owner_user_id)
        if (
            next_owner is None
            or not next_owner.is_active
            or not next_owner.email_verified
        ):
            raise TransferRejected("new owner not eligible")

        old_label = device.name or device.device_uid
        if not old_label.strip() or "\r" in old_label or "\n" in old_label:
            old_label = device.device_uid

        # Old owner and invited members lose access together. The physical
        # identity and archived measurements stay in the database.
        for membership in memberships:
            db.delete(membership)
        # The physical reset erases sm039; do not give the next owner the
        # previous owner's alarm thresholds from the backend either.
        alarm_configuration = db.get(DeviceAlarmConfiguration, device.id)
        if alarm_configuration is not None:
            db.delete(alarm_configuration)
        db.flush()
        db.add(
            DeviceMembership(
                device_id=device.id,
                user_id=next_owner.id,
                role="owner",
            )
        )
        device.name = None
        for channel in db.scalars(
            select(DeviceChannel).where(DeviceChannel.device_id == device.id)
        ):
            channel.name = None

        transfer.state = "completed"
        transfer.effective_at = current
        db.add_all(
            [
                DeviceTransferNotice(
                    transfer_id=transfer.id,
                    user_id=transfer.previous_owner_user_id,
                    phase="completed",
                    body=completed_transfer_notice(old_label, current),
                    created_at=current,
                ),
                DeviceTransferNotice(
                    transfer_id=transfer.id,
                    user_id=transfer.next_owner_user_id,
                    phase="completed",
                    body=new_owner_transfer_notice(device.device_uid, current),
                    created_at=current,
                ),
            ]
        )
        db.commit()
        return transfer
    except TransferRejected:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise TransferRejected("conflicting transfer completion") from error
    except Exception:
        db.rollback()
        raise
