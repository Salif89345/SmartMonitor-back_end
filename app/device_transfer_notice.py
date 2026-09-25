"""French V1 wording for a transfer caused by a physical factory reset.

This module formats notices only. It does not authorize or perform a transfer.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


TRANSFER_DELAY = timedelta(minutes=1)
TRANSFER_CAUSE = "physical_factory_reset_reassociation"
_PARIS = ZoneInfo("Europe/Paris")
_MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def _local_effective_date(effective_at: datetime) -> str:
    if effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise ValueError("effective_at must be timezone-aware")

    local = effective_at.astimezone(_PARIS)
    return (
        f"{local.day} {_MONTHS[local.month - 1]} {local.year} "
        f"à {local.hour:02d}h{local.minute:02d}"
    )


def _device_label(label: str) -> str:
    clean = label.strip()
    if not clean or any(ch in clean for ch in "\r\n"):
        raise ValueError("device label must be a nonempty single line")
    return clean


def pending_transfer_notice(device_label: str, expected_at: datetime) -> str:
    """Pre-notice emitted when a verified physical transfer becomes pending."""
    return (
        f"Une action physique sur l'appareil {_device_label(device_label)} "
        "a déclenché une demande de réassociation. "
        "Son retrait de votre compte est prévu le "
        f"{_local_effective_date(expected_at)}. "
        "Ce n'est pas une anomalie logicielle."
    )


def completed_transfer_notice(device_label: str, effective_at: datetime) -> str:
    """Result notice; effective_at must be the actual database commit time."""
    return (
        f"Le retrait de l'appareil {_device_label(device_label)} de votre "
        "compte a été déclenché par une action physique sur cet appareil.\n\n"
        f"Date d'effet : {_local_effective_date(effective_at)}."
    )


def new_owner_transfer_notice(device_uid: str, effective_at: datetime) -> str:
    """Confirmation visible to the new owner, without the old owner's label."""
    return (
        f"L'appareil {_device_label(device_uid)} a été associé à votre compte "
        f"le {_local_effective_date(effective_at)} après une action physique "
        "sur l'appareil."
    )
