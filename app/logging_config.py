import logging


VALID_LOG_LEVELS = (
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
)


def resolve_log_level(
    app_env: str,
    configured_level: str | None,
) -> str:
    """Return the explicit level or the safe profile default."""

    if configured_level is None or not configured_level.strip():
        return "DEBUG" if app_env == "dev" else "INFO"

    level = configured_level.strip().upper()

    if level not in VALID_LOG_LEVELS:
        raise RuntimeError(
            "LOG_LEVEL must be one of: "
            + ", ".join(VALID_LOG_LEVELS)
        )

    return level


def configure_logging(level_name: str) -> None:
    """Configure SmartMonitor loggers without replacing Uvicorn handlers."""

    level = getattr(logging, level_name)
    logging.getLogger("smartmonitor").setLevel(level)
