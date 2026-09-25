"""SM-015-SEC factory-reset proof primitive (not an exposed API).

An authenticated proof needs a unique, protected factory key for each device.
This module does not issue keys, store challenges, transfer ownership, or
claim that the current bench ESP32 has such a key. A production endpoint must
also persist one-time challenges and reject reused reset generations.
"""

import hashlib
import hmac
import re
from struct import pack


_DOMAIN = b"SmartMonitor/SM-015-SEC/factory-reset/v1\x00"
_UID = re.compile(r"SM-[0-9A-F]{12}\Z")
_HEX_32_BYTES = re.compile(r"[0-9A-F]{64}\Z")
_MAX_GENERATION = 0xFFFFFFFF


def reset_proof_message(
    device_uid: str,
    completed_generation: int,
    challenge_hex: str,
) -> bytes:
    """Encode a backend challenge for one completed physical reset."""
    if not isinstance(device_uid, str) or _UID.fullmatch(device_uid) is None:
        raise ValueError("invalid device UID")
    if (
        isinstance(completed_generation, bool)
        or not isinstance(completed_generation, int)
        or not 1 <= completed_generation <= _MAX_GENERATION
    ):
        raise ValueError("invalid completed reset generation")
    if (
        not isinstance(challenge_hex, str)
        or _HEX_32_BYTES.fullmatch(challenge_hex) is None
    ):
        raise ValueError("invalid reset challenge")
    return (
        _DOMAIN
        + device_uid.encode("ascii")
        + pack(">I", completed_generation)
        + bytes.fromhex(challenge_hex)
    )


def verify_reset_proof(
    *,
    factory_key: bytes,
    device_uid: str,
    completed_generation: int,
    challenge_hex: str,
    tag_hex: str,
) -> bool:
    """Verify HMAC-SHA256; caller must check challenge/epoch single-use.

    This function deliberately refuses malformed inputs. The key must be
    unique to the device and must never be the MQTT or association secret.
    """
    if not isinstance(factory_key, bytes) or len(factory_key) != 32:
        raise ValueError("invalid factory key")
    message = reset_proof_message(
        device_uid, completed_generation, challenge_hex
    )
    if not isinstance(tag_hex, str) or _HEX_32_BYTES.fullmatch(tag_hex) is None:
        return False
    expected = hmac.new(factory_key, message, hashlib.sha256).digest()
    return hmac.compare_digest(expected, bytes.fromhex(tag_hex))
