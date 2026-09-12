from dataclasses import dataclass
from datetime import datetime
from threading import Lock


@dataclass(frozen=True)
class PowerChannelState:
    channel_id: int
    last_measured_at: datetime | None


class PowerIngestionCache:
    """
    Etat RAM minimal pour filtrer les /state avant PostgreSQL.

    La cle est le couple :
        (mqtt_device_id, channel_key)

    Un SmartMonitor peut donc maintenir plusieurs canaux independants.
    """

    def __init__(self):
        self._lock = Lock()
        self._states: dict[
            tuple[str, str],
            PowerChannelState,
        ] = {}

    @staticmethod
    def _key(
        mqtt_device_id: str,
        channel_key: str,
    ) -> tuple[str, str]:
        return (
            mqtt_device_id,
            channel_key,
        )

    def get(
        self,
        mqtt_device_id: str,
        channel_key: str,
    ) -> PowerChannelState | None:
        with self._lock:
            return self._states.get(
                self._key(
                    mqtt_device_id,
                    channel_key,
                )
            )

    def set_if_absent(
        self,
        mqtt_device_id: str,
        channel_key: str,
        state: PowerChannelState,
    ) -> PowerChannelState:
        key = self._key(
            mqtt_device_id,
            channel_key,
        )

        with self._lock:
            existing = self._states.get(
                key
            )

            if existing is not None:
                return existing

            self._states[key] = state

            return state

    def record_measurement(
        self,
        mqtt_device_id: str,
        channel_key: str,
        channel_id: int,
        measured_at: datetime,
    ) -> None:
        key = self._key(
            mqtt_device_id,
            channel_key,
        )

        with self._lock:
            existing = self._states.get(
                key
            )

            if (
                existing is not None
                and existing.channel_id
                != channel_id
            ):
                return

            if (
                existing is not None
                and existing.last_measured_at
                is not None
                and measured_at
                < existing.last_measured_at
            ):
                return

            self._states[key] = (
                PowerChannelState(
                    channel_id=channel_id,
                    last_measured_at=(
                        measured_at
                    ),
                )
            )
