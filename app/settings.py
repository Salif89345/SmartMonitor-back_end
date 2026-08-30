import os

from dotenv import load_dotenv


def _split_csv(
    value: str | None,
) -> list[str]:
    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def _parse_bool(
    name: str,
    default: bool = False,
) -> bool:
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    normalized_value = raw_value.strip().lower()

    if normalized_value in ("1", "true", "yes", "on"):
        return True

    if normalized_value in ("0", "false", "no", "off"):
        return False

    raise RuntimeError(
        f"{name} must be a boolean value"
    )


APP_ENV = os.getenv(
    "APP_ENV",
    "dev",
).strip().lower()

if APP_ENV not in (
    "dev",
    "prod",
):
    raise RuntimeError(
        "APP_ENV must be 'dev' or 'prod'"
    )

if APP_ENV == "dev":
    load_dotenv()


DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")


MQTT_HOST = os.getenv(
    "MQTT_HOST",
    "127.0.0.1",
)

MQTT_PORT = int(
    os.getenv(
        "MQTT_PORT",
        "1883",
    )
)

MQTT_USERNAME = os.getenv(
    "MQTT_USERNAME"
)

MQTT_PASSWORD = os.getenv(
    "MQTT_PASSWORD"
)

MQTT_CLIENT_ID = os.getenv(
    "MQTT_CLIENT_ID",
    "smartmonitor-backend-v1",
)

MQTT_KEEPALIVE = int(
    os.getenv(
        "MQTT_KEEPALIVE",
        "30",
    )
)

MQTT_TLS_ENABLED = _parse_bool(
    "MQTT_TLS_ENABLED",
    False,
)

MQTT_CA_CERT_PATH = os.getenv(
    "MQTT_CA_CERT_PATH"
)

if MQTT_TLS_ENABLED and not MQTT_CA_CERT_PATH:
    raise RuntimeError(
        "MQTT_CA_CERT_PATH is required "
        "when MQTT_TLS_ENABLED is true"
    )

POWER_HISTORY_INTERVAL_SECONDS = int(
    os.getenv(
        "POWER_HISTORY_INTERVAL_SECONDS",
        "60",
    )
)


JWT_SECRET_KEY = os.getenv(
    "JWT_SECRET_KEY"
)

JWT_ALGORITHM = os.getenv(
    "JWT_ALGORITHM",
    "HS256",
)

JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv(
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
        "30",
    )
)


EMAIL_VERIFICATION_SECRET = os.getenv(
    "EMAIL_VERIFICATION_SECRET"
)


RESEND_API_KEY = os.getenv(
    "RESEND_API_KEY"
)

EMAIL_FROM = os.getenv(
    "EMAIL_FROM",
    "SmartMonitor <onboarding@resend.dev>",
)


if APP_ENV == "dev":
    API_ALLOWED_HOSTS = _split_csv(
        os.getenv(
            "API_ALLOWED_HOSTS",
            "localhost,127.0.0.1",
        )
    )
else:
    API_ALLOWED_HOSTS = _split_csv(
        os.getenv(
            "API_ALLOWED_HOSTS"
        )
    )


CORS_ALLOWED_ORIGINS = _split_csv(
    os.getenv(
        "CORS_ALLOWED_ORIGINS"
    )
)


if APP_ENV == "prod":
    required_prod_variables = (
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "MQTT_HOST",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
        "JWT_SECRET_KEY",
        "EMAIL_VERIFICATION_SECRET",
        "RESEND_API_KEY",
        "EMAIL_FROM",
        "API_ALLOWED_HOSTS",
    )

    missing_prod_variables = [
        variable_name
        for variable_name
        in required_prod_variables
        if not os.getenv(variable_name)
    ]

    if missing_prod_variables:
        raise RuntimeError(
            "Missing required production "
            "configuration: "
            + ", ".join(
                missing_prod_variables
            )
        )

    if "*" in API_ALLOWED_HOSTS:
        raise RuntimeError(
            "API_ALLOWED_HOSTS cannot contain "
            "'*' in production"
        )

    if "*" in CORS_ALLOWED_ORIGINS:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS cannot contain "
            "'*' in production"
        )