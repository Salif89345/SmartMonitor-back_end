import hashlib
import json
import unittest

from app.mqtt_client import MqttManager


DEVICE_UID = "SM-A1B2C3D4E5F6"
MQTT_DEVICE_ID = "smartmonitor-a1b2c3d4e5f6"
NONCE = "00112233445566778899AABBCCDDEEFF"


class Message:
    topic = (
        "smartmonitor/provisioning/"
        f"{DEVICE_UID}/claim-proof"
    )

    def __init__(self, nonce: str = NONCE):
        digest = hashlib.sha256(
            nonce.encode("ascii")
        ).hexdigest().upper()

        self.payload = json.dumps(
            {
                "device_uid": DEVICE_UID,
                "proof_sha256": digest,
                "mqtt_device_id": MQTT_DEVICE_ID,
            }
        ).encode("utf-8")


class ClaimProofTests(unittest.TestCase):
    def setUp(self):
        self.manager = MqttManager()
        self.manager._handle_claim_proof_message(
            Message()
        )

    def test_wrong_nonce_does_not_consume_proof(self):
        wrong = self.manager.reserve_claim_proof(
            device_uid=DEVICE_UID,
            nonce="F" * 32,
        )

        self.assertIsNone(wrong)

        valid = self.manager.reserve_claim_proof(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.assertIsNotNone(valid)

    def test_reservation_blocks_concurrent_claim_and_can_release(self):
        first = self.manager.reserve_claim_proof(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.assertIsNotNone(first)

        second = self.manager.reserve_claim_proof(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.assertIsNone(second)

        reservation_id, _ = first

        self.manager.release_claim_proof(
            device_uid=DEVICE_UID,
            reservation_id=reservation_id,
        )

        retry = self.manager.reserve_claim_proof(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.assertIsNotNone(retry)

    def test_committed_proof_is_single_use_and_duplicate_safe(self):
        reservation = (
            self.manager.reserve_claim_proof(
                device_uid=DEVICE_UID,
                nonce=NONCE,
            )
        )

        self.assertIsNotNone(reservation)

        reservation_id, mqtt_device_id = (
            reservation
        )

        self.assertEqual(
            mqtt_device_id,
            MQTT_DEVICE_ID,
        )

        self.assertTrue(
            self.manager.commit_claim_proof(
                device_uid=DEVICE_UID,
                reservation_id=reservation_id,
            )
        )

        # Une republication QoS 0 identique ne doit pas rouvrir
        # une preuve déjà consommée.
        self.manager._handle_claim_proof_message(
            Message()
        )

        replay = self.manager.reserve_claim_proof(
            device_uid=DEVICE_UID,
            nonce=NONCE,
        )

        self.assertIsNone(replay)

    def test_new_physical_window_can_replace_old_digest(self):
        reservation = (
            self.manager.reserve_claim_proof(
                device_uid=DEVICE_UID,
                nonce=NONCE,
            )
        )

        self.assertIsNotNone(reservation)

        reservation_id, _ = reservation

        self.assertTrue(
            self.manager.commit_claim_proof(
                device_uid=DEVICE_UID,
                reservation_id=reservation_id,
            )
        )

        new_nonce = "A" * 32

        self.manager._handle_claim_proof_message(
            Message(new_nonce)
        )

        new_claim = self.manager.reserve_claim_proof(
            device_uid=DEVICE_UID,
            nonce=new_nonce,
        )

        self.assertIsNotNone(new_claim)


if __name__ == "__main__":
    unittest.main()
