import hashlib
import hmac
import unittest

from app.factory_reset_proof import reset_proof_message, verify_reset_proof


KEY = bytes(range(32))
UID = "SM-24367DBD2A58"
CHALLENGE = "A1" * 32


def tag_for(uid=UID, generation=1, challenge=CHALLENGE, key=KEY):
    message = reset_proof_message(uid, generation, challenge)
    return hmac.new(key, message, hashlib.sha256).hexdigest().upper()


class FactoryResetProofTests(unittest.TestCase):
    def test_valid_device_specific_proof(self):
        self.assertTrue(verify_reset_proof(
            factory_key=KEY,
            device_uid=UID,
            completed_generation=1,
            challenge_hex=CHALLENGE,
            tag_hex=tag_for(),
        ))

    def test_wrong_key_challenge_generation_or_tag_fails(self):
        base = dict(
            factory_key=KEY,
            device_uid=UID,
            completed_generation=1,
            challenge_hex=CHALLENGE,
            tag_hex=tag_for(),
        )
        for change in (
            dict(factory_key=bytes(reversed(KEY))),
            dict(device_uid="SM-AAAAAAAAAAAA"),
            dict(challenge_hex="B2" * 32),
            dict(completed_generation=2),
            dict(tag_hex="00" * 32),
        ):
            with self.subTest(change=change):
                self.assertFalse(verify_reset_proof(**(base | change)))

    def test_malformed_identity_challenge_or_incomplete_reset_is_rejected(self):
        for uid, generation, challenge in (
            ("atelier", 1, CHALLENGE),
            (UID, 0, CHALLENGE),
            (UID, -1, CHALLENGE),
            (UID, True, CHALLENGE),
            (UID, 0x100000000, CHALLENGE),
            (UID, 1, "00"),
        ):
            with self.subTest(uid=uid, generation=generation):
                with self.assertRaises(ValueError):
                    reset_proof_message(uid, generation, challenge)

    def test_simple_association_nonce_is_not_a_reset_proof(self):
        self.assertFalse(verify_reset_proof(
            factory_key=KEY,
            device_uid=UID,
            completed_generation=1,
            challenge_hex=CHALLENGE,
            tag_hex="00112233445566778899AABBCCDDEEFF",
        ))

    def test_factory_key_must_be_individual_32_byte_secret(self):
        with self.assertRaises(ValueError):
            verify_reset_proof(
                factory_key=b"shared-password",
                device_uid=UID,
                completed_generation=1,
                challenge_hex=CHALLENGE,
                tag_hex=tag_for(),
            )


if __name__ == "__main__":
    unittest.main()
