import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.settings import (
    EMAIL_VERIFICATION_SECRET,
)


VERIFICATION_CODE_DIGITS = 6
VERIFICATION_CODE_TTL_MINUTES = 10
VERIFICATION_RESEND_COOLDOWN_SECONDS = 60
VERIFICATION_SEND_WINDOW_MINUTES = 60
VERIFICATION_MAX_SENDS_PER_WINDOW = 5
VERIFICATION_FAILED_WINDOW_MINUTES = 15
VERIFICATION_MAX_FAILED_ATTEMPTS = 5


if not EMAIL_VERIFICATION_SECRET:
    raise RuntimeError(
        "EMAIL_VERIFICATION_SECRET is not configured"
    )


def generate_verification_code() -> str:
    value = secrets.randbelow(
        10 ** VERIFICATION_CODE_DIGITS
    )

    return f"{value:06d}"


def verification_code_digest(
    user_id: int,
    code: str,
) -> str:
    message = (
        f"{user_id}:{code}"
    ).encode("utf-8")

    return hmac.new(
        EMAIL_VERIFICATION_SECRET.encode(
            "utf-8"
        ),
        message,
        hashlib.sha256,
    ).hexdigest()


def verification_code_expires_at(
    now: datetime | None = None,
) -> datetime:
    current_time = (
        now
        if now is not None
        else datetime.now(timezone.utc)
    )

    return current_time + timedelta(
        minutes=VERIFICATION_CODE_TTL_MINUTES
    )


def is_verification_code_expired(
    expires_at: datetime,
    now: datetime | None = None,
) -> bool:
    current_time = (
        now
        if now is not None
        else datetime.now(timezone.utc)
    )

    return expires_at <= current_time


def verification_code_matches(
    user_id: int,
    code: str,
    expected_digest: str,
) -> bool:
    actual_digest = verification_code_digest(
        user_id,
        code,
    )

    return hmac.compare_digest(
        actual_digest,
        expected_digest,
    )
