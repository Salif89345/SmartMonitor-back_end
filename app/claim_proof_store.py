import hashlib
import hmac
import threading
import time
import uuid

from collections.abc import Callable


CLAIM_PROOF_TTL_SECONDS = 120.0


class ClaimProofStore:
    """Thread-safe in-memory lifecycle for device claim proofs."""

    def __init__(
        self,
        *,
        ttl_seconds: float = CLAIM_PROOF_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        reservation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._reservation_id_factory = (
            reservation_id_factory
            if reservation_id_factory is not None
            else lambda: str(uuid.uuid4())
        )
        self._lock = threading.Lock()
        self._proofs: dict[str, dict] = {}
        self._consumed_proofs: dict[
            tuple[str, str],
            float,
        ] = {}

    def _prune(self, now: float) -> None:
        expired_device_uids = [
            device_uid
            for device_uid, proof in self._proofs.items()
            if proof["expires_at"] <= now
        ]

        for device_uid in expired_device_uids:
            self._proofs.pop(device_uid, None)

        expired_consumed_proofs = [
            key
            for key, expires_at in self._consumed_proofs.items()
            if expires_at <= now
        ]

        for key in expired_consumed_proofs:
            self._consumed_proofs.pop(key, None)

    def register(
        self,
        *,
        device_uid: str,
        proof_sha256: str,
        mqtt_device_id: str,
    ) -> bool:
        now = self._clock()
        normalized_digest = proof_sha256.lower()
        consumed_key = (device_uid, normalized_digest)

        with self._lock:
            self._prune(now)

            if consumed_key in self._consumed_proofs:
                return False

            existing_proof = self._proofs.get(device_uid)

            if (
                existing_proof is not None
                and existing_proof.get("reservation_id") is not None
                and existing_proof["proof_sha256"]
                == normalized_digest
            ):
                return False

            self._proofs[device_uid] = {
                "proof_sha256": normalized_digest,
                "mqtt_device_id": mqtt_device_id,
                "expires_at": now + self._ttl_seconds,
                "reservation_id": None,
                "reservation_expires_at": 0.0,
            }

        return True

    def reserve(
        self,
        *,
        device_uid: str,
        nonce: str,
    ) -> tuple[str, str] | None:
        if (
            len(nonce) != 32
            or any(
                character not in "0123456789ABCDEF"
                for character in nonce
            )
        ):
            return None

        calculated_sha256 = hashlib.sha256(
            nonce.encode("ascii")
        ).hexdigest()
        now = self._clock()

        with self._lock:
            self._prune(now)
            proof = self._proofs.get(device_uid)

            if proof is None:
                return None

            if not hmac.compare_digest(
                calculated_sha256,
                proof["proof_sha256"],
            ):
                return None

            reservation_id = proof.get("reservation_id")
            reservation_expires_at = proof.get(
                "reservation_expires_at",
                0.0,
            )

            if (
                reservation_id is not None
                and reservation_expires_at > now
            ):
                return None

            reservation_id = self._reservation_id_factory()
            proof["reservation_id"] = reservation_id
            proof["reservation_expires_at"] = proof["expires_at"]
            mqtt_device_id = proof["mqtt_device_id"]

        return reservation_id, mqtt_device_id

    def release(
        self,
        *,
        device_uid: str,
        reservation_id: str,
    ) -> None:
        with self._lock:
            proof = self._proofs.get(device_uid)

            if (
                proof is None
                or proof.get("reservation_id") != reservation_id
            ):
                return

            proof["reservation_id"] = None
            proof["reservation_expires_at"] = 0.0

    def commit(
        self,
        *,
        device_uid: str,
        reservation_id: str,
    ) -> bool:
        now = self._clock()

        with self._lock:
            self._prune(now)
            proof = self._proofs.get(device_uid)

            if (
                proof is None
                or proof.get("reservation_id") != reservation_id
            ):
                return False

            digest = proof["proof_sha256"]
            self._proofs.pop(device_uid, None)
            self._consumed_proofs[
                (device_uid, digest)
            ] = now + self._ttl_seconds

        return True
