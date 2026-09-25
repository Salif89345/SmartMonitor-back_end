"""Atomic backend ownership switch after a trusted MQTT cutover receipt.

There is deliberately no public route or scheduler here. The current shared
prototype MQTT account cannot produce the required per-device cutover receipt.
"""

from datetime import datetime, timezone

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
    DeviceChannel,
    DeviceMembership,
    DeviceTransfer,
    DeviceTransferMqttCutover,
    DeviceTransferNotice,
    User,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite test storage loses tzinfo; PostgreSQL does not.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def complete_due_transfer(
    db: Session, *, transfer_id: int, now: datetime | None = None
) -> DeviceTransfer:
    """Switch rights once, only after the 1-minute deadline and broker cutover.

    The broker worker must first revoke the old device credential, verify the
    new individual credential, then write DeviceTransferMqttCutover. A failed
    database commit leaves the request pending for an idempotent retry.
    """
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

        cutover = db.get(DeviceTransferMqttCutover, transfer.id)
        if (
            cutover is None
            or cutover.device_uid != device.device_uid
            or not cutover.broker_reference.strip()
            or _utc(cutover.old_access_revoked_at) < _utc(transfer.requested_at)
            or _utc(cutover.new_access_verified_at) < _utc(transfer.requested_at)
            or _utc(cutover.old_access_revoked_at) > current
            or _utc(cutover.new_access_verified_at) > current
        ):
            raise TransferRejected("verified MQTT credential cutover required")

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
