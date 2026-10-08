"""Authenticated, feature-gated handoff from association to SM-015 transfer.

The requesting account must first present a fresh MQTT association proof.
The reset challenge is then bound to that account. A separate device-generated
reset proof is required to stage the one-minute transfer. Factory keys come
only from a trusted server-side provider; there is deliberately no default.
"""

from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.device_transfer_bench_factory import configured_bench_factory_key
from app.device_transfer_store import (
    TransferRejected,
    issue_reset_challenge,
    stage_verified_transfer,
)
from app.models import (
    Device,
    DeviceMembership,
    DeviceResetChallenge,
    DeviceTransfer,
    User,
)
from app.mqtt_client import mqtt_manager
from app.settings import SM015_TRANSFER_STAGING_ENABLED


router = APIRouter(prefix="/api/v1/device-transfers", tags=["device-transfers"])


class FactoryKeyProvider(Protocol):
    def key_for_device(self, device_uid: str) -> bytes | None: ...


def get_factory_key_provider() -> FactoryKeyProvider | None:
    """Only the explicitly configured development bench can provide a key."""
    return configured_bench_factory_key()


class TransferChallengeRequest(BaseModel):
    device_uid: str = Field(pattern=r"^SM-[0-9A-F]{12}$")
    nonce: str = Field(pattern=r"^[0-9A-F]{32}$")


class TransferChallengePublic(BaseModel):
    challenge_id: str
    challenge_hex: str
    expires_at: datetime


class TransferStageRequest(BaseModel):
    device_uid: str = Field(pattern=r"^SM-[0-9A-F]{12}$")
    challenge_id: str = Field(min_length=36, max_length=36)
    challenge_hex: str = Field(pattern=r"^[0-9A-F]{64}$")
    reset_generation: int = Field(gt=0, le=0xFFFFFFFF)
    tag_hex: str = Field(pattern=r"^[0-9A-F]{64}$")


class TransferPublic(BaseModel):
    transfer_id: int
    state: str
    not_before: datetime
    effective_at: datetime | None


def _require_enabled_user(user: User) -> None:
    if not SM015_TRANSFER_STAGING_ENABLED:
        raise HTTPException(status_code=404, detail="Transfer unavailable")
    if not user.is_active or not user.email_verified:
        raise HTTPException(status_code=403, detail="Verified account required")


def _factory_key(
    provider: FactoryKeyProvider | None, device_uid: str
) -> bytes:
    try:
        key = provider.key_for_device(device_uid) if provider is not None else None
    except Exception as error:
        raise HTTPException(
            status_code=503, detail="Factory identity unavailable"
        ) from error
    if not isinstance(key, bytes) or len(key) != 32:
        raise HTTPException(status_code=503, detail="Factory identity unavailable")
    return key


@router.post("/challenges", response_model=TransferChallengePublic)
def create_transfer_challenge(
    payload: TransferChallengeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    factory_keys: FactoryKeyProvider | None = Depends(get_factory_key_provider),
):
    _require_enabled_user(current_user)
    device = db.scalar(select(Device).where(Device.device_uid == payload.device_uid))
    owner = (
        db.scalar(
            select(DeviceMembership).where(
                DeviceMembership.device_id == device.id,
                DeviceMembership.role == "owner",
            )
        )
        if device is not None
        else None
    )
    if owner is None or owner.user_id == current_user.id or not device.mqtt_device_id:
        raise HTTPException(status_code=409, detail="Device not transferable")
    if db.scalar(
        select(DeviceTransfer.id).where(
            DeviceTransfer.device_id == device.id,
            DeviceTransfer.state == "pending",
        )
    ) is not None:
        raise HTTPException(status_code=409, detail="Device transfer pending")
    _factory_key(factory_keys, payload.device_uid)

    reservation = mqtt_manager.reserve_claim_proof(
        device_uid=payload.device_uid, nonce=payload.nonce
    )
    if reservation is None:
        raise HTTPException(status_code=409, detail="Association proof unavailable")
    reservation_id, mqtt_device_id = reservation
    if mqtt_device_id != device.mqtt_device_id:
        mqtt_manager.release_claim_proof(
            device_uid=payload.device_uid, reservation_id=reservation_id
        )
        raise HTTPException(status_code=409, detail="Device MQTT identity mismatch")
    if not mqtt_manager.commit_claim_proof(
        device_uid=payload.device_uid, reservation_id=reservation_id
    ):
        mqtt_manager.release_claim_proof(
            device_uid=payload.device_uid, reservation_id=reservation_id
        )
        raise HTTPException(status_code=409, detail="Association proof expired")
    try:
        challenge = issue_reset_challenge(
            db,
            device_uid=payload.device_uid,
            requested_by_user_id=current_user.id,
        )
    except TransferRejected as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return TransferChallengePublic(
        challenge_id=challenge.id,
        challenge_hex=challenge.challenge_hex,
        expires_at=challenge.expires_at,
    )


@router.post("/requests", response_model=TransferPublic)
def create_transfer_request(
    payload: TransferStageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    factory_keys: FactoryKeyProvider | None = Depends(get_factory_key_provider),
):
    _require_enabled_user(current_user)
    challenge = db.get(DeviceResetChallenge, payload.challenge_id)
    device = db.get(Device, challenge.device_id) if challenge is not None else None
    if (
        challenge is None
        or challenge.requested_by_user_id != current_user.id
        or device is None
        or device.device_uid != payload.device_uid
    ):
        raise HTTPException(status_code=409, detail="Reset challenge unavailable")
    key = _factory_key(factory_keys, payload.device_uid)
    try:
        transfer = stage_verified_transfer(
            db,
            device_uid=payload.device_uid,
            next_owner_user_id=current_user.id,
            reset_generation=payload.reset_generation,
            challenge_id=payload.challenge_id,
            challenge_hex=payload.challenge_hex,
            tag_hex=payload.tag_hex,
            factory_key=key,
        )
    except TransferRejected as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return TransferPublic(
        transfer_id=transfer.id,
        state=transfer.state,
        not_before=transfer.not_before,
        effective_at=transfer.effective_at,
    )


@router.get("/requests/{transfer_id}", response_model=TransferPublic)
def get_transfer_request(
    transfer_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_enabled_user(current_user)
    transfer = db.get(DeviceTransfer, transfer_id)
    if transfer is None or transfer.next_owner_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return TransferPublic(
        transfer_id=transfer.id,
        state=transfer.state,
        not_before=transfer.not_before,
        effective_at=transfer.effective_at,
    )
