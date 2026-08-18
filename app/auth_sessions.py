import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuthSession


REFRESH_TOKEN_BYTES = 64


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(
        REFRESH_TOKEN_BYTES
    )


def hash_refresh_token(
    refresh_token: str,
) -> str:
    return hashlib.sha256(
        refresh_token.encode("utf-8")
    ).hexdigest()


def create_auth_session(
    db: Session,
    user_id: int,
    now: datetime | None = None,
) -> tuple[str, AuthSession]:
    current_time = (
        now
        if now is not None
        else datetime.now(timezone.utc)
    )

    refresh_token = generate_refresh_token()

    session = AuthSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(
            refresh_token
        ),
        created_at=current_time,
        last_used_at=current_time,
        revoked_at=None,
    )

    db.add(session)

    return refresh_token, session


def find_refresh_session_for_update(
    db: Session,
    refresh_token: str,
) -> AuthSession | None:
    digest = hash_refresh_token(
        refresh_token
    )

    return db.scalar(
        select(AuthSession)
        .where(
            AuthSession.refresh_token_hash
            == digest
        )
        .with_for_update()
    )


def revoke_all_active_sessions(
    db: Session,
    user_id: int,
    now: datetime | None = None,
) -> int:
    current_time = (
        now
        if now is not None
        else datetime.now(timezone.utc)
    )

    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(
            revoked_at=current_time
        )
    )

    return int(
        result.rowcount or 0
    )
