from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[SM-012-SEC] ECHEC: {message}")


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    require(path.is_file(), f"fichier absent: {relative_path}")
    content = path.read_bytes()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252")


ignore = read(".gitignore")
example = read(".env.example")
settings = read("app/settings.py")
mqtt = read("app/mqtt_client.py")

for expected in (".env", ".env.*", ".secrets/"):
    require(expected in ignore, f"exclusion Git absente: {expected}")

for variable in (
    "DB_PASSWORD",
    "MQTT_PASSWORD",
    "COMMAND_AUTH_KEYS_PATH",
    "JWT_SECRET_KEY",
    "EMAIL_VERIFICATION_SECRET",
    "RESEND_API_KEY",
):
    require(variable in example, f"variable absente du modele: {variable}")
    require(variable in settings, f"variable non geree: {variable}")

require(
    "json.dumps(\n                        payload" in mqtt,
    "publication MQTT attendue introuvable",
)
require(
    "print(\n                payload" not in mqtt,
    "payload de commande affiche dans les logs",
)

result = subprocess.run(
    ["git", "ls-files"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=False,
)
if result.returncode == 0:
    tracked = {
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
    }
    forbidden = {
        ".env",
        ".secrets/command_authorization_keys.json",
    }
    require(
        not (tracked & forbidden),
        "un fichier de secrets local est suivi par Git",
    )

print("[SM-012-SEC] Protection des secrets backend : PASS")
