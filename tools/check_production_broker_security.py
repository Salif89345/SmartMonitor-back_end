from pathlib import Path
import csv
import sys


root = Path(__file__).resolve().parents[1]

security = (root / "app" / "mqtt_security.py").read_text(encoding="utf-8")
settings = (root / "app" / "settings.py").read_text(encoding="utf-8")
client = (root / "app" / "mqtt_client.py").read_text(encoding="utf-8")
gitignore = (root / ".gitignore").read_text(encoding="utf-8")
production_example = (root / ".env.production.example").read_text(
    encoding="utf-8"
)
policy_root = root / "deploy" / "mqtt" / "production"

checks = (
    ("production MQTT port is fixed to 8883", "port != 8883" in security),
    (
        "production broker host is allow-listed",
        "MQTT_ALLOWED_PROD_HOSTS" in settings
        and "MQTT_HOST is not in MQTT_ALLOWED_PROD_HOSTS" in security,
    ),
    (
        "production client ID is distinct",
        "smartmonitor-backend-prod-" in security,
    ),
    (
        "credential rotations are identified",
        "MQTT_CREDENTIAL_ID" in settings and "credential_id" in security,
    ),
    (
        "TLS verifies hostname and certificate",
        "check_hostname = True" in security
        and "verify_mode = ssl.CERT_REQUIRED" in security,
    ),
    (
        "TLS minimum is 1.2",
        "minimum_version = ssl.TLSVersion.TLSv1_2" in security,
    ),
    (
        "MQTT client uses the hardened context",
        "tls_set_context" in client and "build_mqtt_tls_context" in client,
    ),
    (
        "production environment example stays versioned",
        "!.env.production.example" in gitignore,
    ),
    (
        "production example contains placeholders only",
        "MQTT_PASSWORD=CHANGE_ME" in production_example
        and "prod-broker.example.invalid" in production_example,
    ),
)

failed = [name for name, passed in checks if not passed]

for csv_name in ("backend-acl.csv", "device-acl.template.csv"):
    csv_path = policy_root / csv_name
    if not csv_path.is_file():
        failed.append(f"missing ACL file: {csv_name}")
        continue

    with csv_path.open(encoding="utf-8", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    expected_headers = {"clientid", "username", "topic", "action", "access"}
    if not rows or set(rows[0]) != expected_headers:
        failed.append(f"invalid ACL structure: {csv_name}")
        continue

    if any(row["access"] != "allow" for row in rows):
        failed.append(f"unexpected ACL permission: {csv_name}")

device_acl = (policy_root / "device-acl.template.csv").read_text(
    encoding="utf-8"
)
if "smartmonitor/+/" in device_acl:
    failed.append("device ACL contains a cross-device wildcard")

if failed:
    for failure in failed:
        print(f"[SM-062-SEC] ECHEC: {failure}")
    sys.exit(1)

print(
    "[SM-062-SEC] Garde-fous backend et politiques ACL : "
    f"{len(checks) + 5} controles OK."
)
