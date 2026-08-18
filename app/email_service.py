import json
from urllib.error import (
    HTTPError,
    URLError,
)
from urllib.request import (
    Request,
    urlopen,
)

from app.settings import (
    EMAIL_FROM,
    RESEND_API_KEY,
)


RESEND_EMAIL_URL = (
    "https://api.resend.com/emails"
)

EMAIL_TIMEOUT_SECONDS = 10

USER_AGENT = (
    "SmartMonitor-Backend/0.1"
)


class EmailDeliveryError(Exception):
    pass


def send_verification_email(
    to_email: str,
    code: str,
    idempotency_key: str | None = None,
) -> str:
    if not RESEND_API_KEY:
        raise EmailDeliveryError(
            "Email service is not configured"
        )

    if (
        len(code) != 6
        or not code.isdigit()
    ):
        raise ValueError(
            "Verification code must contain "
            "exactly 6 digits"
        )

    payload = {
        "from": EMAIL_FROM,
        "to": [
            to_email,
        ],
        "subject": (
            "Votre code de verification "
            "SmartMonitor"
        ),
        "text": (
            "Votre code de verification "
            f"SmartMonitor est : {code}\n\n"
            "Ce code expire dans 10 minutes.\n"
            "Si vous n'etes pas a l'origine "
            "de cette demande, ignorez cet email."
        ),
    }

    headers = {
        "Authorization": (
            "Bearer " + RESEND_API_KEY
        ),
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    if idempotency_key:
        headers[
            "Idempotency-Key"
        ] = idempotency_key

    request = Request(
        RESEND_EMAIL_URL,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=EMAIL_TIMEOUT_SECONDS,
        ) as response:
            body = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except HTTPError as exc:
        raise EmailDeliveryError(
            "Email delivery failed "
            f"(HTTP {exc.code})"
        ) from None

    except (
        URLError,
        TimeoutError,
        json.JSONDecodeError,
    ):
        raise EmailDeliveryError(
            "Email delivery failed"
        ) from None

    email_id = body.get("id")

    if not email_id:
        raise EmailDeliveryError(
            "Email provider returned "
            "an invalid response"
        )

    return str(email_id)
