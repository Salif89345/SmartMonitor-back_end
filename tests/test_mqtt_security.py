import tempfile
import unittest
from pathlib import Path
import ssl

from app.mqtt_security import (
    build_mqtt_tls_context,
    validate_mqtt_security_config,
)


class MqttSecurityConfigurationTests(unittest.TestCase):
    def validate(self, **overrides) -> None:
        values = {
            "app_env": "dev",
            "host": "broker.example.test",
            "port": 1883,
            "username": None,
            "password": None,
            "client_id": "smartmonitor-backend-test",
            "tls_enabled": False,
            "ca_cert_path": None,
            "deployment_environment": "dev",
            "allowed_prod_hosts": (),
            "credential_id": None,
        }
        values.update(overrides)
        validate_mqtt_security_config(**values)

    def test_dev_allows_anonymous_local_broker(self):
        self.validate()

    def test_partial_credentials_are_always_rejected(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "must be configured together",
        ):
            self.validate(username="backend", password=None)

    def test_production_requires_tls(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "MQTT_TLS_ENABLED must be true",
        ):
            self.validate(
                app_env="prod",
                port=8883,
                username="backend",
                password="secret",
                deployment_environment="prod",
                allowed_prod_hosts=("broker.example.test",),
                credential_id="prod-2026-09-a",
                client_id="smartmonitor-backend-prod-primary",
            )

    def test_production_requires_authenticated_credentials(self):
        with tempfile.NamedTemporaryFile() as ca_file:
            with self.assertRaisesRegex(
                RuntimeError,
                "authenticated MQTT credentials are required",
            ):
                self.validate(
                    app_env="prod",
                    port=8883,
                    tls_enabled=True,
                    ca_cert_path=ca_file.name,
                    deployment_environment="prod",
                    allowed_prod_hosts=("broker.example.test",),
                    credential_id="prod-2026-09-a",
                    client_id="smartmonitor-backend-prod-primary",
                )

    def test_production_requires_a_ca_file(self):
        missing_path = Path(tempfile.gettempdir()) / (
            "smartmonitor-sm057-sec-missing-ca.pem"
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "does not reference a readable file",
        ):
            self.validate(
                app_env="prod",
                port=8883,
                username="backend",
                password="secret",
                tls_enabled=True,
                ca_cert_path=str(missing_path),
                deployment_environment="prod",
                allowed_prod_hosts=("broker.example.test",),
                credential_id="prod-2026-09-a",
                client_id="smartmonitor-backend-prod-primary",
            )

    def test_complete_production_configuration_is_accepted(self):
        with tempfile.NamedTemporaryFile() as ca_file:
            self.validate(
                app_env="prod",
                port=8883,
                username="backend",
                password="secret",
                tls_enabled=True,
                ca_cert_path=ca_file.name,
                deployment_environment="prod",
                allowed_prod_hosts=("broker.example.test",),
                credential_id="prod-2026-09-a",
                client_id="smartmonitor-backend-prod-primary",
            )

    def test_production_requires_the_mqtt_tls_port(self):
        with tempfile.NamedTemporaryFile() as ca_file:
            with self.assertRaisesRegex(
                RuntimeError,
                "MQTT_PORT must be 8883",
            ):
                self.validate(
                    app_env="prod",
                    username="backend",
                    password="secret",
                    tls_enabled=True,
                    ca_cert_path=ca_file.name,
                    deployment_environment="prod",
                    allowed_prod_hosts=("broker.example.test",),
                    credential_id="prod-2026-09-a",
                    client_id="smartmonitor-backend-prod-primary",
                )

    def test_production_rejects_a_non_production_broker(self):
        with tempfile.NamedTemporaryFile() as ca_file:
            with self.assertRaisesRegex(
                RuntimeError,
                "MQTT_HOST is not in MQTT_ALLOWED_PROD_HOSTS",
            ):
                self.validate(
                    app_env="prod",
                    host="lab-broker.example.test",
                    port=8883,
                    username="backend",
                    password="secret",
                    tls_enabled=True,
                    ca_cert_path=ca_file.name,
                    deployment_environment="prod",
                    allowed_prod_hosts=("prod-broker.example.test",),
                    credential_id="prod-2026-09-a",
                    client_id="smartmonitor-backend-prod-primary",
                )

    def test_production_requires_a_dedicated_client_id(self):
        with tempfile.NamedTemporaryFile() as ca_file:
            with self.assertRaisesRegex(
                RuntimeError,
                "production prefix",
            ):
                self.validate(
                    app_env="prod",
                    port=8883,
                    username="backend",
                    password="secret",
                    tls_enabled=True,
                    ca_cert_path=ca_file.name,
                    deployment_environment="prod",
                    allowed_prod_hosts=("broker.example.test",),
                    credential_id="prod-2026-09-a",
                )

    def test_production_requires_a_rotation_identifier(self):
        with tempfile.NamedTemporaryFile() as ca_file:
            with self.assertRaisesRegex(
                RuntimeError,
                "MQTT_CREDENTIAL_ID is required",
            ):
                self.validate(
                    app_env="prod",
                    port=8883,
                    username="backend",
                    password="secret",
                    tls_enabled=True,
                    ca_cert_path=ca_file.name,
                    deployment_environment="prod",
                    allowed_prod_hosts=("broker.example.test",),
                    client_id="smartmonitor-backend-prod-primary",
                )

    def test_tls_context_verifies_hostname_and_server_certificate(self):
        ca_path = (
            Path(__file__).resolve().parents[1]
            / "certs"
            / "emqxsl-ca.crt"
        )
        context = build_mqtt_tls_context(str(ca_path))

        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreaterEqual(
            context.minimum_version,
            ssl.TLSVersion.TLSv1_2,
        )


if __name__ == "__main__":
    unittest.main()
