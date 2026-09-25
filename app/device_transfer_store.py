"""SM-015-SEC transfer staging, intentionally not exposed by an API route.

This stores a one-use reset challenge, a timed transfer request and the old
owner's in-app pre-notice. It deliberately does NOT change memberships or
enable controls: factory-key custody, MQTT revocation and per-owner history
isolation must exist before a real transfer may be completed.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.device_transfer_notice import (
    TRANSFER_CAUSE,
    TRANSFER_DELAY,
    pending_transfer_notice,
)
from app.factory_reset_proof import verify_reset_proof
from app.models import (
    Device,
    DeviceMembership,
    DeviceResetChallenge,
    DeviceTransfer,
    DeviceTransferNotice,
    User,
)


CHALLENGE_LIFETIME = timedelta(minutes=2)


class TransferRejected(ValueError):
    """A transfer prerequisite is missing; no ownership change occurred."""


@dataclass(frozen=True)
class IssuedResetChallenge:
    id: str
    challenge_hex: str
    expires_at: datetime


def _now_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _db_utc(value: datetime) -> datetime:
    # SQLite used by unit tests loses timezone; PostgreSQL keeps it.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def issue_reset_challenge(
    db: Session, *, device_uid: str, now: datetime | None = None
) -> IssuedResetChallenge:
    """Internal only; do not expose until the production enrollment is ready."""
    current = _now_utc(now)
    device = db.scalar(select(Device).where(Device.device_uid == device_uid))
    if device is None:
        raise TransferRejected("device not registered")
    challenge_hex = secrets.token_hex(32).upper()
    challenge = DeviceResetChallenge(
        id=str(uuid4()),
        device_id=device.id,
        challenge_digest=hashlib.sha256(bytes.fromhex(challenge_hex)).hexdigest(),
        created_at=current,
        expires_at=current + CHALLENGE_LIFETIME,
    )
    db.add(challenge)
    db.commit()
    return IssuedResetChallenge(
        id=challenge.id,
        challenge_hex=challenge_hex,
        expires_at=current + CHALLENGE_LIFETIME,
    )


def stage_verified_transfer(
    db: Session,
    *,
    device_uid: str,
    next_owner_user_id: int,
    reset_generation: int,
    challenge_id: str,
    challenge_hex: str,
    tag_hex: str,
    factory_key: bytes,
    now: datetime | None = None,
) -> DeviceTransfer:
    """Record a verified request and durable pre-notice, not a real transfer.

    `factory_key` must come from a future trusted per-device key store, never
    from the requesting user or an API payload. No caller currently invokes
    this function from a public route.
    """
    current = _now_utc(now)
    try:
        device = db.scalar(
            select(Device).where(Device.device_uid == device_uid).with_for_update()
        )
        if device is None:
            raise TransferRejected("device not registered")

        owner = db.scalar(
            select(DeviceMembership).where(
                DeviceMembership.device_id == device.id,
                DeviceMembership.role == "owner",
            )
        )
        next_owner = db.get(User, next_owner_user_id)
        if owner is None or next_owner is None:
            raise TransferRejected("owner account missing")
        if (
            owner.user_id == next_owner_user_id
            or not next_owner.is_active
            or not next_owner.email_verified
        ):
            raise TransferRejected("new owner not eligible")

        pending = db.scalar(
            select(DeviceTransfer.id).where(
                DeviceTransfer.device_id == device.id,
                DeviceTransfer.state == "pending",
            )
        )
        if pending is not None:
            raise TransferRejected("transfer already pending")
        last_generation = db.scalar(
            select(func.max(DeviceTransfer.reset_generation)).where(
                DeviceTransfer.device_id == device.id
            )
        ) or 0
        if (
            isinstance(reset_generation, bool)
            or not isinstance(reset_generation, int)
            or reset_generation <= last_generation
        ):
            raise TransferRejected("reset generation already used")

        challenge = db.scalar(
            select(DeviceResetChallenge)
            .where(
                DeviceResetChallenge.id == challenge_id,
                DeviceResetChallenge.device_id == device.id,
            )
            .with_for_update()
        )
        if (
            challenge is None
            or challenge.consumed_at is not None
            or _db_utc(challenge.expires_at) <= current
        ):
            raise TransferRejected("reset challenge expired or used")
        try:
            supplied_digest = hashlib.sha256(
                bytes.fromhex(challenge_hex)
            ).hexdigest()
            digest_matches = hmac.compare_digest(
                challenge.challenge_digest, supplied_digest
            )
            proof_valid = digest_matches and verify_reset_proof(
                factory_key=factory_key,
                device_uid=device_uid,
                completed_generation=reset_generation,
                challenge_hex=challenge_hex,
                tag_hex=tag_hex,
            )
        except (ValueError, TypeError):
            proof_valid = False
        if not proof_valid:
            raise TransferRejected("physical reset proof invalid")

        expected_at = current + TRANSFER_DELAY
        transfer = DeviceTransfer(
            device_id=device.id,
            previous_owner_user_id=owner.user_id,
            next_owner_user_id=next_owner_user_id,
            challenge_id=challenge.id,
            reset_generation=reset_generation,
            state="pending",
            cause=TRANSFER_CAUSE,
            requested_at=current,
            not_before=expected_at,
        )
        challenge.consumed_at = current
        db.add(transfer)
        db.flush()
        label = device.name or device.device_uid
        if not label.strip() or "\r" in label or "\n" in label:
            label = device.device_uid
        db.add(
            DeviceTransferNotice(
                transfer_id=transfer.id,
                user_id=owner.user_id,
                phase="pending",
                body=pending_transfer_notice(label, expected_at),
                created_at=current,
            )
        )
        db.commit()
        return transfer
    except TransferRejected:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise TransferRejected("conflicting transfer request") from error
