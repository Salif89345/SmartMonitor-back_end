from collections.abc import Sequence
from pathlib import Path
import ssl


def _is_present(value: str | None) -> bool:
    return bool(value and value.strip())


def validate_mqtt_security_config(
    *,
    app_env: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    client_id: str,
    tls_enabled: bool,
    ca_cert_path: str | None,
    deployment_environment: str = "dev",
    allowed_prod_hosts: Sequence[str] = (),
    credential_id: str | None = None,
) -> None:
    """Fail closed when MQTT authentication or production TLS is incomplete."""
    problems: list[str] = []

    if not _is_present(host):
        problems.append("MQTT_HOST is empty")

    if not 1 <= port <= 65535:
        problems.append("MQTT_PORT is outside 1..65535")

    if not _is_present(client_id):
        problems.append("MQTT_CLIENT_ID is empty")

    username_present = _is_present(username)
    password_present = _is_present(password)

    if username_present != password_present:
        problems.append(
            "MQTT_USERNAME and MQTT_PASSWORD must be configured together"
        )

    if app_env == "prod":
        normalized_host = host.strip().lower()
        normalized_allowed_hosts = {
            allowed_host.strip().lower()
            for allowed_host in allowed_prod_hosts
            if allowed_host.strip()
        }

        if deployment_environment != "prod":
            problems.append(
                "MQTT_DEPLOYMENT_ENV must be 'prod' in production"
            )

        if port != 8883:
            problems.append("MQTT_PORT must be 8883 in production")

        if not tls_enabled:
            problems.append("MQTT_TLS_ENABLED must be true in production")

        if not username_present or not password_present:
            problems.append(
                "authenticated MQTT credentials are required in production"
            )

        if not _is_present(ca_cert_path):
            problems.append(
                "MQTT_CA_CERT_PATH is required in production"
            )
        elif not Path(ca_cert_path).is_file():
            problems.append(
                "MQTT_CA_CERT_PATH does not reference a readable file"
            )

        if not normalized_allowed_hosts:
            problems.append(
                "MQTT_ALLOWED_PROD_HOSTS is required in production"
            )
        elif normalized_host not in normalized_allowed_hosts:
            problems.append(
                "MQTT_HOST is not in MQTT_ALLOWED_PROD_HOSTS"
            )

        if not client_id.startswith("smartmonitor-backend-prod-"):
            problems.append(
                "MQTT_CLIENT_ID must use the production prefix "
                "'smartmonitor-backend-prod-'"
            )

        if not _is_present(credential_id):
            problems.append(
                "MQTT_CREDENTIAL_ID is required in production"
            )

    if problems:
        raise RuntimeError(
            "Invalid MQTT security configuration: "
            + "; ".join(problems)
        )


def build_mqtt_tls_context(
    ca_cert_path: str,
) -> ssl.SSLContext:
    """Build the only TLS policy accepted by the SmartMonitor backend."""
    context = ssl.create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH,
        cafile=ca_cert_path,
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context
