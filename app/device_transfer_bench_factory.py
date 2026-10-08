"""Opt-in factory-key bridge for the isolated SM-015 development bench.

The key is supplied to the backend process by the local bench launcher; it is
never accepted from an HTTP request. This adapter is unavailable in prod and
is not a production key-custody design.
"""

import os
import re

from app.settings import APP_ENV, SM015_TRANSFER_STAGING_ENABLED


_UID = re.compile(r"SM-[0-9A-F]{12}\Z")
_KEY = re.compile(r"[0-9A-Fa-f]{64}\Z")


class BenchFactoryKeyProvider:
    def __init__(self, *, device_uid: str, key: bytes):
        if _UID.fullmatch(device_uid) is None or len(key) != 32:
            raise ValueError("invalid bench factory identity")
        self._device_uid = device_uid
        self._key = key

    def key_for_device(self, device_uid: str) -> bytes | None:
        return self._key if device_uid == self._device_uid else None


def configured_bench_factory_key() -> BenchFactoryKeyProvider | None:
    if APP_ENV != "dev" or not SM015_TRANSFER_STAGING_ENABLED:
        return None
    device_uid = os.getenv("SM015_BENCH_DEVICE_UID", "")
    key_hex = os.getenv("SM015_BENCH_FACTORY_KEY_HEX", "")
    if _UID.fullmatch(device_uid) is None or _KEY.fullmatch(key_hex) is None:
        return None
    return BenchFactoryKeyProvider(device_uid=device_uid, key=bytes.fromhex(key_hex))
