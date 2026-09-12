import hashlib
import unittest

from app.claim_proof_store import ClaimProofStore


DEVICE_UID = "SM-A1B2C3D4E5F6"
MQTT_DEVICE_ID = "smartmonitor-a1b2c3d4e5f6"
NONCE = "00112233445566778899AABBCCDDEEFF"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def proof_digest(nonce: str = NONCE) -> str:
    return hashlib.sha256(
        nonce.encode("ascii")
    ).hexdigest().upper()


class ClaimProofStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = ClaimProofStore(
            ttl_seconds=120.0,
            clock=self.clock,
            reservation_id_factory=lambda: "reservation-1",
        )
        self.assertTrue(
            self.store.register(
                device_uid=DEVICE_UID,
                proof_sha256=proof_digest(),
                mqtt_device_id=MQTT_DEVICE_ID,
            )
        )

    def test_reserve_checks_nonce_and_blocks_concurrent_use(self):
        self.assertIsNone(
            self.store.reserve(
                device_uid=DEVICE_UID,
                nonce="F" * 32,
            )
        )

        reservation = self.store.reserve(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.assertEqual(
            reservation,
            ("reservation-1", MQTT_DEVICE_ID),
        )
        self.assertIsNone(
            self.store.reserve(
                device_uid=DEVICE_UID,
                nonce=NONCE,
            )
        )

    def test_release_allows_a_new_reservation(self):
        reservation_id, _ = self.store.reserve(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.store.release(
            device_uid=DEVICE_UID,
            reservation_id=reservation_id,
        )

        self.assertIsNotNone(
            self.store.reserve(
                device_uid=DEVICE_UID,
                nonce=NONCE,
            )
        )

    def test_commit_prevents_replay_until_ttl_expires(self):
        reservation_id, _ = self.store.reserve(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.assertTrue(
            self.store.commit(
                device_uid=DEVICE_UID,
                reservation_id=reservation_id,
            )
        )
        self.assertFalse(
            self.store.register(
                device_uid=DEVICE_UID,
                proof_sha256=proof_digest(),
                mqtt_device_id=MQTT_DEVICE_ID,
            )
        )

        self.clock.now += 121.0

        self.assertTrue(
            self.store.register(
                device_uid=DEVICE_UID,
                proof_sha256=proof_digest(),
                mqtt_device_id=MQTT_DEVICE_ID,
            )
        )

    def test_unreserved_proof_expires(self):
        self.clock.now += 121.0

        self.assertIsNone(
            self.store.reserve(
                device_uid=DEVICE_UID,
                nonce=NONCE,
            )
        )

    def test_same_reserved_proof_cannot_be_republished(self):
        self.assertIsNotNone(
            self.store.reserve(
                device_uid=DEVICE_UID,
                nonce=NONCE,
            )
        )

        self.assertFalse(
            self.store.register(
                device_uid=DEVICE_UID,
                proof_sha256=proof_digest(),
                mqtt_device_id=MQTT_DEVICE_ID,
            )
        )


if __name__ == "__main__":
    unittest.main()
