import logging
import uuid
from datetime import (
    datetime,
    timedelta,
    timezone,
)

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth_sessions import (
    create_auth_session,
    find_refresh_session_for_update,
    revoke_all_active_sessions,
)
from app.database import get_db
from app.email_service import (
    EmailDeliveryError,
    send_verification_email,
)
from app.email_verification import (
    VERIFICATION_RESEND_COOLDOWN_SECONDS,
    VERIFICATION_SEND_WINDOW_MINUTES,
    VERIFICATION_MAX_SENDS_PER_WINDOW,
    VERIFICATION_FAILED_WINDOW_MINUTES,
    VERIFICATION_MAX_FAILED_ATTEMPTS,
    generate_verification_code,
    is_verification_code_expired,
    verification_code_digest,
    verification_code_expires_at,
    verification_code_matches,
)
from app.models import (
    EmailVerification,
    User,
)
from app.rate_limit import (
    enforce_login_rate_limit,
    enforce_register_rate_limit,
)
from app.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshTokenRequest,
    ResendVerificationRequest,
    UserCreate,
    UserPublic,
    VerificationMessageResponse,
    VerifyEmailRequest,
)
from app.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


logger = logging.getLogger(
    "uvicorn.error"
)


router = APIRouter(
    prefix="/api/v1/auth",
    tags=["auth"],
)


bearer_scheme = HTTPBearer(
    auto_error=False
)


GENERIC_RESEND_MESSAGE = (
    "If this account requires verification, "
    "a code will be sent when allowed."
)

INVALID_VERIFICATION_DETAIL = (
    "Invalid or expired verification code"
)

EMAIL_VERIFICATION_REQUIRED_DETAIL = (
    "Email verification required"
)


def _new_verification_code(
    user_id: int,
    now: datetime,
) -> tuple[str, str, datetime]:
    code = generate_verification_code()

    digest = verification_code_digest(
        user_id,
        code,
    )

    expires_at = verification_code_expires_at(
        now
    )

    return (
        code,
        digest,
        expires_at,
    )


def _get_verification_for_update(
    db: Session,
    user_id: int,
) -> EmailVerification | None:
    return db.scalar(
        select(EmailVerification)
        .where(
            EmailVerification.user_id
            == user_id
        )
        .with_for_update()
    )


def _record_verification_failure(
    verification: EmailVerification,
    now: datetime,
) -> None:
    window_started_at = (
        verification.failed_window_started_at
    )

    window_expired = (
        window_started_at is None
        or now
        >= (
            window_started_at
            + timedelta(
                minutes=(
                    VERIFICATION_FAILED_WINDOW_MINUTES
                )
            )
        )
    )

    if window_expired:
        verification.failed_window_started_at = now
        verification.failed_attempts = 1
        verification.locked_until = None

    else:
        verification.failed_attempts += 1

    if (
        verification.failed_attempts
        >= VERIFICATION_MAX_FAILED_ATTEMPTS
    ):
        verification.locked_until = (
            verification.failed_window_started_at
            + timedelta(
                minutes=(
                    VERIFICATION_FAILED_WINDOW_MINUTES
                )
            )
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    user_id = decode_access_token(
        credentials.credentials
    )

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    user = db.get(
        User,
        user_id,
    )

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                EMAIL_VERIFICATION_REQUIRED_DETAIL
            ),
        )

    return user


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    enforce_register_rate_limit(
        request
    )

    normalized_email = payload.email.lower()

    existing_user = db.scalar(
        select(User).where(
            User.email == normalized_email
        )
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        )

    user = User(
        email=normalized_email,
        password_hash=hash_password(
            payload.password
        ),
        is_active=True,
        email_verified=False,
    )

    db.add(user)

    try:
        db.flush()

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        ) from None

    now = datetime.now(
        timezone.utc
    )

    code, digest, expires_at = (
        _new_verification_code(
            user.id,
            now,
        )
    )

    verification = EmailVerification(
        user_id=user.id,
        code_digest=digest,
        expires_at=expires_at,
        failed_attempts=0,
        failed_window_started_at=None,
        locked_until=None,
        last_sent_at=now,
        send_window_started_at=now,
        send_count=1,
    )

    db.add(
        verification
    )

    try:
        db.flush()

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        ) from None

    try:
        send_verification_email(
            to_email=normalized_email,
            code=code,
            idempotency_key=(
                "register-"
                + str(user.id)
                + "-"
                + str(uuid.uuid4())
            ),
        )

    except EmailDeliveryError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Verification email unavailable"
            ),
        ) from None

    try:
        db.commit()

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        ) from None

    db.refresh(user)

    return user


@router.post(
    "/verify-email",
    response_model=VerificationMessageResponse,
)
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
):
    normalized_email = payload.email.lower()

    user = db.scalar(
        select(User).where(
            User.email == normalized_email
        )
    )

    if (
        user is None
        or user.email_verified
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_DETAIL,
        )

    now = datetime.now(timezone.utc)

    verification = (
        _get_verification_for_update(
            db,
            user.id,
        )
    )

    if verification is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_DETAIL,
        )

    if (
        verification.locked_until is not None
        and now < verification.locked_until
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_DETAIL,
        )

    if is_verification_code_expired(
        verification.expires_at,
        now=now,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_DETAIL,
        )

    if not verification_code_matches(
        user.id,
        payload.code,
        verification.code_digest,
    ):
        _record_verification_failure(
            verification,
            now,
        )

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_DETAIL,
        )

    user.email_verified = True

    db.delete(
        verification
    )

    db.commit()

    return {
        "message": "Email verified",
    }


@router.post(
    "/resend-verification",
    response_model=VerificationMessageResponse,
)
def resend_verification(
    payload: ResendVerificationRequest,
    db: Session = Depends(get_db),
):
    normalized_email = payload.email.lower()

    user = db.scalar(
        select(User).where(
            User.email == normalized_email
        )
    )

    if (
        user is None
        or user.email_verified
    ):
        return {
            "message": GENERIC_RESEND_MESSAGE,
        }

    now = datetime.now(timezone.utc)

    verification = (
        _get_verification_for_update(
            db,
            user.id,
        )
    )

    if verification is not None:
        cooldown_ends_at = (
            verification.last_sent_at
            + timedelta(
                seconds=(
                    VERIFICATION_RESEND_COOLDOWN_SECONDS
                )
            )
        )

        if now < cooldown_ends_at:
            return {
                "message": GENERIC_RESEND_MESSAGE,
            }

        send_window_expired = (
            now
            >= (
                verification.send_window_started_at
                + timedelta(
                    minutes=(
                        VERIFICATION_SEND_WINDOW_MINUTES
                    )
                )
            )
        )

        if (
            not send_window_expired
            and verification.send_count
            >= VERIFICATION_MAX_SENDS_PER_WINDOW
        ):
            return {
                "message": GENERIC_RESEND_MESSAGE,
            }

    else:
        send_window_expired = True

    code = generate_verification_code()

    code_digest = verification_code_digest(
        user.id,
        code,
    )

    expires_at = verification_code_expires_at(
        now
    )

    if verification is None:
        verification = EmailVerification(
            user_id=user.id,
            code_digest=code_digest,
            expires_at=expires_at,
            failed_attempts=0,
            failed_window_started_at=None,
            locked_until=None,
            last_sent_at=now,
            send_window_started_at=now,
            send_count=1,
        )

        db.add(
            verification
        )

    else:
        verification.code_digest = code_digest
        verification.expires_at = expires_at
        verification.last_sent_at = now

        if send_window_expired:
            verification.send_window_started_at = now
            verification.send_count = 1

        else:
            verification.send_count += 1

    try:
        db.flush()

    except IntegrityError:
        db.rollback()

        return {
            "message": GENERIC_RESEND_MESSAGE,
        }

    try:
        send_verification_email(
            normalized_email,
            code,
            idempotency_key=(
                "resend-"
                + str(user.id)
                + "-"
                + uuid.uuid4().hex
            ),
        )

    except EmailDeliveryError:
        db.rollback()

        logger.warning(
            "Verification resend delivery failed "
            "for user_id=%s",
            user.id,
        )

        return {
            "message": GENERIC_RESEND_MESSAGE,
        }

    db.commit()

    return {
        "message": GENERIC_RESEND_MESSAGE,
    }


@router.post(
    "/login",
    response_model=LoginResponse,
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    enforce_login_rate_limit(
        request
    )

    normalized_email = payload.email.lower()

    user = db.scalar(
        select(User).where(
            User.email == normalized_email
        )
    )

    if (
        user is None
        or not verify_password(
            payload.password,
            user.password_hash,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                EMAIL_VERIFICATION_REQUIRED_DETAIL
            ),
        )

    refresh_token, _ = create_auth_session(
        db,
        user.id,
    )

    db.commit()

    access_token = create_access_token(
        user.id
    )

    return {
        "authenticated": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
    }


@router.post(
    "/refresh",
    response_model=LoginResponse,
)
def refresh_session(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)

    auth_session = (
        find_refresh_session_for_update(
            db,
            payload.refresh_token,
        )
    )

    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = auth_session.user_id

    if auth_session.revoked_at is not None:
        revoke_all_active_sessions(
            db,
            user_id,
            now=now,
        )

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user = db.get(
        User,
        user_id,
    )

    if user is None:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if not user.is_active:
        revoke_all_active_sessions(
            db,
            user.id,
            now=now,
        )

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    if not user.email_verified:
        revoke_all_active_sessions(
            db,
            user.id,
            now=now,
        )

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                EMAIL_VERIFICATION_REQUIRED_DETAIL
            ),
        )

    auth_session.revoked_at = now
    auth_session.last_used_at = now

    new_refresh_token, _ = (
        create_auth_session(
            db,
            user.id,
            now=now,
        )
    )

    db.commit()

    access_token = create_access_token(
        user.id
    )

    return {
        "authenticated": True,
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "user": user,
    }


@router.post(
    "/logout",
    response_model=LogoutResponse,
)
def logout(
    payload: LogoutRequest,
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)

    auth_session = (
        find_refresh_session_for_update(
            db,
            payload.refresh_token,
        )
    )

    if (
        auth_session is not None
        and auth_session.revoked_at is None
    ):
        auth_session.revoked_at = now
        auth_session.last_used_at = now

    db.commit()

    return {
        "message": "Logged out",
    }


@router.get(
    "/me",
    response_model=UserPublic,
)
def me(
    current_user: User = Depends(
        get_current_user
    ),
):
    return current_user
