from pathlib import Path


root = Path(__file__).resolve().parents[1]
settings = (root / "app" / "settings.py").read_text(encoding="utf-8")
main = (root / "app" / "main.py").read_text(encoding="utf-8")
mqtt = (root / "app" / "mqtt_client.py").read_text(encoding="utf-8")
logging_config = (root / "app" / "logging_config.py").read_text(encoding="utf-8")

for marker in (
    "LOG_LEVEL = resolve_log_level(",
    "configure_logging(LOG_LEVEL)",
    "DEBUG\" if app_env == \"dev\" else \"INFO",
):
    if marker not in settings + main + logging_config:
        raise SystemExit(f"Configuration backend absente: {marker}")

for marker in (
    'logger.debug(\n            "[MQTT] Message received:',
    'logger.debug(\n                "[MQTT] Power measurement stored:',
):
    if marker not in mqtt:
        raise SystemExit(f"Trace MQTT frequente non filtree: {marker}")

for marker in (
    'logger.error(\n                "[MQTT] Connection failed:',
    'logger.exception(\n                    "[MQTT] Ingestion worker failed:',
):
    if marker not in mqtt:
        raise SystemExit(f"Erreur backend non structuree: {marker}")

print("[OP-015] Profils backend DEV/PROD et niveaux MQTT : OK.")
