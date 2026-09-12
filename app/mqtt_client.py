import json
import logging
import math
from queue import Empty, Full, Queue
import threading
import uuid

from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.claim_proof_store import ClaimProofStore
from app.database import SessionLocal
from app.device_events import create_device_event_by_mqtt_id
from app.device_live_state import live_state_store
from app.ingestion_cache import (
    PowerChannelState,
    PowerIngestionCache,
)
from app.mqtt_contract import (
    MqttContractError,
    build_command_payload,
    validate_response_payload,
)
from app.mqtt_ingestion import IncomingMqttMessage
from app.measurement_contract import (
    normalize_electrical_measurement,
)
from app.settings import (
    MQTT_CA_CERT_PATH,
    MQTT_CLIENT_ID,
    MQTT_HOST,
    MQTT_KEEPALIVE,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_TLS_ENABLED,
    MQTT_USERNAME,
    POWER_HISTORY_INTERVAL_SECONDS,
    MQTT_INGESTION_QUEUE_SIZE,
)
from app.models import (
    Device,
    DeviceChannel,
    PowerMeasurement,
)
from app.power_daily_summary import (
    local_date_for_timestamp,
    summarize_completed_days,
)
from app.data_retention import (
    cleanup_channel_history,
)

MQTT_SUBSCRIPTIONS = (
    "smartmonitor/+/state",
    "smartmonitor/+/status",
    "smartmonitor/+/outage",
    "smartmonitor/+/response",
    "smartmonitor/provisioning/+/claim-proof",
)

POWER_CHANNEL_KEYS = (
    "power_1",
    "power_2",
    "power_3",
    "power_4",
)


logger = logging.getLogger("smartmonitor.mqtt")


class DeviceUnavailableError(
    RuntimeError
):
    """
    Raised when a SmartMonitor is not known
    to be online and ready for commands.
    """

    pass


class MqttManager:
    def __init__(self):
        self._connected = threading.Event()

        self._state_lock = threading.Lock()
        self._last_error: str | None = None
        self._received_messages = 0
        self._last_message_topic: str | None = None

        self._pending_lock = threading.Lock()
        self._pending_responses: dict[
            str,
            dict,
        ] = {}

        self._device_status_lock = (
            threading.Lock()
        )

        self._device_statuses: dict[
            str,
            str,
        ] = {}

        self._daily_summary_checked_date = {}

        self._ingestion_queue: Queue[
            IncomingMqttMessage | None
        ] = Queue(
            maxsize=MQTT_INGESTION_QUEUE_SIZE
        )
        self._ingestion_lock = threading.Lock()
        self._ingestion_worker: threading.Thread | None = None
        self._ingestion_accepting = False
        self._ingestion_dropped_messages = 0

        self._power_ingestion_cache = (
            PowerIngestionCache()
        )

        self._claim_proof_store = ClaimProofStore()

        self.client = mqtt.Client(
            callback_api_version=
                mqtt.CallbackAPIVersion.VERSION2,
            client_id=MQTT_CLIENT_ID,
            protocol=mqtt.MQTTv311,
        )

        if MQTT_USERNAME:
            self.client.username_pw_set(
                MQTT_USERNAME,
                MQTT_PASSWORD,
            )

        if MQTT_TLS_ENABLED:
            self.client.tls_set(
                ca_certs=MQTT_CA_CERT_PATH,
            )

        self.client.reconnect_delay_set(
            min_delay=1,
            max_delay=30,
        )

        self.client.on_connect = (
            self._on_connect
        )

        self.client.on_disconnect = (
            self._on_disconnect
        )

        self.client.on_subscribe = (
            self._on_subscribe
        )

        self.client.on_message = (
            self._on_message
        )

    def _on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ):
        if reason_code != 0:
            self._connected.clear()

            with self._state_lock:
                self._last_error = (
                    "Connection refused: "
                    f"{reason_code}"
                )

            with self._device_status_lock:
                self._device_statuses.clear()

            logger.error(
                "[MQTT] Connection failed: %s",
                reason_code,
            )

            return

        self._connected.set()

        with self._state_lock:
            self._last_error = None

        # Après une nouvelle connexion du backend
        # au broker, aucun ancien état appareil
        # ne doit être considéré comme fiable.
        #
        # Les messages retained /status reçus après
        # les abonnements reconstruiront l'état réel.
        with self._device_status_lock:
            self._device_statuses.clear()

        logger.info("[MQTT] Backend connected")

        for topic in MQTT_SUBSCRIPTIONS:
            result, message_id = (
                client.subscribe(
                    topic,
                    qos=0,
                )
            )

            if (
                result
                == mqtt.MQTT_ERR_SUCCESS
            ):
                logger.debug(
                    "[MQTT] Subscription requested: %s",
                    topic,
                )

            else:
                logger.error(
                    "[MQTT] Subscription request failed: %s | code: %s",
                    topic,
                    result,
                )

    def _on_subscribe(
        self,
        client,
        userdata,
        message_id,
        reason_code_list,
        properties,
    ):
        failures = [
            reason_code
            for reason_code
            in reason_code_list
            if reason_code.is_failure
        ]

        if failures:
            logger.error(
                "[MQTT] Subscription refused: %s",
                failures,
            )

            with self._state_lock:
                self._last_error = (
                    "Subscription refused: "
                    f"{failures}"
                )

            return

        logger.debug(
            "[MQTT] Subscription confirmed"
        )

    def _enqueue_ingestion_message(
        self,
        kind: str,
        topic: str,
        payload: bytes,
    ) -> None:
        with self._ingestion_lock:
            accepting = self._ingestion_accepting

        if not accepting:
            print(
                "[MQTT] Ingestion skipped: worker unavailable",
                "| topic:",
                topic,
            )
            return

        try:
            self._ingestion_queue.put_nowait(
                IncomingMqttMessage(
                    kind=kind,
                    topic=str(topic),
                    payload=bytes(payload),
                )
            )
        except Full:
            with self._ingestion_lock:
                self._ingestion_dropped_messages += 1

            logger.error(
                "[MQTT] Ingestion queue full: message dropped"
                " | kind: %s | topic: %s",
                kind,
                topic,
            )

    def _run_ingestion_worker(self) -> None:
        while True:
            task = self._ingestion_queue.get()

            try:
                if task is None:
                    return

                if task.kind == "state":
                    self._handle_state_message(task)
                elif task.kind == "status":
                    self._handle_status_message(task)
                elif task.kind == "outage":
                    self._handle_outage_message(task)

            except Exception as exc:
                logger.exception(
                    "[MQTT] Ingestion worker failed:"
                    " | kind: %s | error: %s",
                    task.kind if task is not None else "stop",
                    type(exc).__name__,
                )

            finally:
                self._ingestion_queue.task_done()

    def _start_ingestion_worker(self) -> None:
        with self._ingestion_lock:
            if (
                self._ingestion_worker is not None
                and self._ingestion_worker.is_alive()
            ):
                return

            self._ingestion_accepting = True
            self._ingestion_worker = threading.Thread(
                target=self._run_ingestion_worker,
                name="smartmonitor-mqtt-ingestion",
                daemon=True,
            )
            self._ingestion_worker.start()

    def _stop_ingestion_worker(self) -> None:
        with self._ingestion_lock:
            self._ingestion_accepting = False
            worker = self._ingestion_worker

        if worker is None or not worker.is_alive():
            return

        self._ingestion_queue.put(None, timeout=5)
        worker.join(timeout=10)

        if worker.is_alive():
            print("[MQTT] Ingestion worker did not stop in time")


    def _on_message(
        self,
        client,
        userdata,
        message,
    ):
        with self._state_lock:
            self._received_messages += 1
            self._last_message_topic = (
                message.topic
            )

        logger.debug(
            "[MQTT] Message received: %s | bytes: %s",
            message.topic,
            len(message.payload),
        )

        if (
            message.topic.startswith(
                "smartmonitor/provisioning/"
            )
            and message.topic.endswith(
                "/claim-proof"
            )
        ):
            self._handle_claim_proof_message(
                message
            )

            return

        for suffix, kind in (
            ("/state", "state"),
            ("/status", "status"),
            ("/outage", "outage"),
        ):
            if message.topic.endswith(suffix):
                self._enqueue_ingestion_message(
                    kind,
                    message.topic,
                    message.payload,
                )
                return

        if not message.topic.endswith(
            "/response"
        ):
            return

        try:
            payload = json.loads(
                message.payload.decode(
                    "utf-8"
                )
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            print(
                "[MQTT] Invalid response payload:",
                message.topic,
            )

            return

        request_id = payload.get(
            "request_id"
        )

        if not isinstance(
            request_id,
            str,
        ):
            return

        with self._pending_lock:
            pending = (
                self._pending_responses.get(
                    request_id
                )
            )

            if pending is None:
                return

            if (
                message.topic
                != pending[
                    "response_topic"
                ]
            ):
                return

            try:
                validated_response = (
                    validate_response_payload(
                        payload,
                        expected_request_id=request_id,
                    )
                )

            except MqttContractError as error:
                pending[
                    "contract_error"
                ] = str(error)

                pending[
                    "event"
                ].set()

                print(
                    "[MQTT] Invalid response contract:",
                    message.topic,
                    "|",
                    error,
                )

                return

            pending[
                "response"
            ] = validated_response

            pending[
                "event"
            ].set()

    def _handle_claim_proof_message(
        self,
        message,
    ) -> None:
        topic_parts = message.topic.split("/")

        if (
            len(topic_parts) != 4
            or topic_parts[0] != "smartmonitor"
            or topic_parts[1] != "provisioning"
            or topic_parts[3] != "claim-proof"
        ):
            print(
                "[MQTT] Invalid claim proof topic:",
                message.topic,
            )
            return

        topic_device_uid = topic_parts[2]

        try:
            payload = json.loads(
                message.payload.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            print(
                "[MQTT] Invalid claim proof payload:",
                message.topic,
            )
            return

        if not isinstance(payload, dict):
            return

        device_uid = payload.get("device_uid")
        proof_sha256 = payload.get("proof_sha256")
        mqtt_device_id = payload.get("mqtt_device_id")

        if (
            not isinstance(device_uid, str)
            or len(device_uid) != 15
            or not device_uid.startswith("SM-")
            or any(
                character not in "0123456789ABCDEF"
                for character in device_uid[3:]
            )
            or device_uid != topic_device_uid
        ):
            print(
                "[MQTT] Invalid claim device UID:",
                message.topic,
            )
            return

        if (
            not isinstance(proof_sha256, str)
            or len(proof_sha256) != 64
            or any(
                character not in "0123456789ABCDEF"
                for character in proof_sha256
            )
        ):
            print(
                "[MQTT] Invalid claim proof digest:",
                device_uid,
            )
            return

        if (
            not isinstance(mqtt_device_id, str)
            or not mqtt_device_id
            or len(mqtt_device_id) > 31
            or any(
                character in "/+#"
                for character in mqtt_device_id
            )
        ):
            print(
                "[MQTT] Invalid claim MQTT identity:",
                device_uid,
            )
            return

        registered = self._claim_proof_store.register(
            device_uid=device_uid,
            proof_sha256=proof_sha256,
            mqtt_device_id=mqtt_device_id,
        )

        if not registered:
            return

        # Le nonce lui-même ne transite jamais par MQTT
        # et n'est jamais journalisé.
        print(
            "[MQTT] Association proof registered:",
            device_uid,
        )

    def reserve_claim_proof(
        self,
        *,
        device_uid: str,
        nonce: str,
    ) -> tuple[str, str] | None:
        return self._claim_proof_store.reserve(
            device_uid=device_uid,
            nonce=nonce,
        )

    def release_claim_proof(
        self,
        *,
        device_uid: str,
        reservation_id: str,
    ) -> None:
        self._claim_proof_store.release(
            device_uid=device_uid,
            reservation_id=reservation_id,
        )

    def commit_claim_proof(
        self,
        *,
        device_uid: str,
        reservation_id: str,
    ) -> bool:
        return self._claim_proof_store.commit(
            device_uid=device_uid,
            reservation_id=reservation_id,
        )


    def _handle_outage_message(
        self,
        message,
    ):
        topic_parts = (
            message.topic.split("/")
        )

        if (
            len(topic_parts) != 3
            or topic_parts[0]
            != "smartmonitor"
            or topic_parts[2]
            != "outage"
            or not topic_parts[1]
        ):
            print(
                "[MQTT] Invalid outage topic:",
                message.topic,
            )

            return

        mqtt_device_id = topic_parts[1]

        try:
            payload = json.loads(
                message.payload.decode("utf-8")
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            print(
                "[MQTT] Invalid outage payload:",
                message.topic,
            )

            return

        if not isinstance(payload, dict):
            print(
                "[MQTT] Invalid outage payload:"
                " JSON object expected",
                "| device:",
                mqtt_device_id,
            )

            return

        schema_version = payload.get(
            "schema_version"
        )
        duration_ms = payload.get(
            "duration_ms"
        )
        wifi_lost = payload.get("wifi_lost")
        mqtt_lost = payload.get("mqtt_lost")
        started_at = payload.get("started_at")
        restored_at = payload.get("restored_at")

        if (
            type(schema_version) is not int
            or schema_version != 1
            or payload.get("event")
            != "network_outage"
            or payload.get("device_id")
            != mqtt_device_id
            or type(duration_ms) is not int
            or duration_ms < 0
            or type(wifi_lost) is not bool
            or type(mqtt_lost) is not bool
            or not self._is_valid_outage_epoch(
                started_at
            )
            or not self._is_valid_outage_epoch(
                restored_at
            )
        ):
            print(
                "[MQTT] Invalid outage report:"
                " contract rejected",
                "| device:",
                mqtt_device_id,
            )

            return

        self._persist_device_event(
            mqtt_device_id=mqtt_device_id,
            event_type="network_outage",
            data={
                "schema_version": schema_version,
                "duration_ms": duration_ms,
                "wifi_lost": wifi_lost,
                "mqtt_lost": mqtt_lost,
                "started_at": started_at,
                "restored_at": restored_at,
            },
        )

    @staticmethod
    def _is_valid_outage_epoch(
        value,
    ) -> bool:
        return (
            value is None
            or (
                type(value) is int
                and value >= 0
            )
        )


    def _handle_status_message(
        self,
        message,
    ):
        topic_parts = (
            message.topic.split("/")
        )

        if (
            len(topic_parts) != 3
            or topic_parts[0]
            != "smartmonitor"
            or topic_parts[2]
            != "status"
        ):
            print(
                "[MQTT] Invalid status topic:",
                message.topic,
            )

            return

        mqtt_device_id = topic_parts[1]

        if not mqtt_device_id:
            print(
                "[MQTT] Invalid status topic:"
                " empty device id"
            )

            return

        try:
            device_status = (
                message.payload
                .decode("utf-8")
                .strip()
                .lower()
            )

        except UnicodeDecodeError:
            print(
                "[MQTT] Invalid status payload:",
                message.topic,
            )

            return

        if device_status not in (
            "online",
            "offline",
        ):
            print(
                "[MQTT] Invalid device status:",
                mqtt_device_id,
                "| value:",
                repr(device_status),
            )

            return

        with self._device_status_lock:
            previous_status = (
                self._device_statuses.get(
                    mqtt_device_id
                )
            )

            self._device_statuses[
                mqtt_device_id
            ] = device_status

        print(
            "[MQTT] Device status updated:",
            mqtt_device_id,
            "| status:",
            device_status,
        )

        # Le premier status re?u apr?s une
        # connexion/reconnexion backend sert
        # uniquement ? reconstruire l'?tat
        # temps r?el. Il peut provenir d'un
        # message MQTT retained et ne doit
        # donc pas cr?er un faux ?v?nement.
        if previous_status is None:
            return

        if previous_status == device_status:
            return

        self._persist_device_event(
            mqtt_device_id=mqtt_device_id,
            event_type=(
                "device_online"
                if device_status == "online"
                else "device_offline"
            ),
        )

    def _persist_device_event(
        self,
        *,
        mqtt_device_id: str,
        event_type: str,
        data: dict | None = None,
    ) -> None:
        db = SessionLocal()

        try:
            event = (
                create_device_event_by_mqtt_id(
                    db,
                    mqtt_device_id=(
                        mqtt_device_id
                    ),
                    event_type=event_type,
                    data=data,
                )
            )

            if event is None:
                db.rollback()

                print(
                    "[EVENT] Device ignored:"
                    " unknown MQTT id",
                    "| device:",
                    mqtt_device_id,
                    "| type:",
                    event_type,
                )

                return

            db.commit()

            print(
                "[EVENT] Device event stored:",
                "| device:",
                mqtt_device_id,
                "| type:",
                event_type,
            )

        except Exception as exc:
            db.rollback()

            print(
                "[EVENT] Persistence failed:",
                "| device:",
                mqtt_device_id,
                "| type:",
                event_type,
                "| error:",
                type(exc).__name__,
            )

        finally:
            db.close()


    def get_device_status(
        self,
        mqtt_device_id: str,
    ) -> str | None:
        with self._device_status_lock:
            return self._device_statuses.get(
                mqtt_device_id
            )

    @staticmethod
    def _is_finite_number(
        value,
    ) -> bool:
        if isinstance(
            value,
            bool,
        ):
            return False

        if not isinstance(
            value,
            (int, float),
        ):
            return False

        return math.isfinite(
            float(value)
        )

    def _get_power_channel_state(
        self,
        mqtt_device_id: str,
        channel_key: str,
    ) -> PowerChannelState | None:
        cached = (
            self._power_ingestion_cache.get(
                mqtt_device_id,
                channel_key,
            )
        )

        if cached is not None:
            return cached

        db = SessionLocal()

        try:
            channel_id = db.scalar(
                select(
                    DeviceChannel.id
                )
                .join(
                    Device,
                    DeviceChannel.device_id
                    == Device.id,
                )
                .where(
                    Device.mqtt_device_id
                    == mqtt_device_id,

                    Device.is_active.is_(
                        True
                    ),

                    DeviceChannel.channel_key
                    == channel_key,

                    DeviceChannel.is_enabled.is_(
                        True
                    ),
                )
            )

            if channel_id is None:
                return None

            last_measured_at = db.scalar(
                select(
                    PowerMeasurement.measured_at
                )
                .where(
                    PowerMeasurement.channel_id
                    == channel_id,
                )
                .order_by(
                    PowerMeasurement
                    .measured_at
                    .desc()
                )
                .limit(1)
            )

        finally:
            db.close()

        return (
            self._power_ingestion_cache
            .set_if_absent(
                mqtt_device_id,
                channel_key,
                PowerChannelState(
                    channel_id=channel_id,
                    last_measured_at=(
                        last_measured_at
                    ),
                ),
            )
        )


    def _extract_energy_blocks(
        self,
        *,
        payload: dict,
        schema_version: int,
        mqtt_device_id: str,
    ) -> dict[str, dict] | None:
        if schema_version == 1:
            energy = payload.get(
                "energy"
            )

            if not isinstance(
                energy,
                dict,
            ):
                print(
                    "[MQTT] State skipped:"
                    " missing energy block",
                    "| device:",
                    mqtt_device_id,
                )

                return None

            return {
                "power_1": energy,
            }

        energy_channels = payload.get(
            "energy_channels"
        )

        if not isinstance(
            energy_channels,
            dict,
        ):
            print(
                "[MQTT] State skipped:"
                " missing energy_channels block",
                "| device:",
                mqtt_device_id,
            )

            return None

        valid_channels: dict[
            str,
            dict,
        ] = {}

        for (
            channel_key,
            energy,
        ) in energy_channels.items():
            if (
                channel_key
                not in POWER_CHANNEL_KEYS
            ):
                print(
                    "[MQTT] State channel skipped:"
                    " unsupported channel",
                    "| device:",
                    mqtt_device_id,
                    "| channel:",
                    channel_key,
                )

                continue

            if not isinstance(
                energy,
                dict,
            ):
                print(
                    "[MQTT] State channel skipped:"
                    " invalid energy block",
                    "| device:",
                    mqtt_device_id,
                    "| channel:",
                    channel_key,
                )

                continue

            valid_channels[
                channel_key
            ] = energy

        return valid_channels


    def _parse_energy_values(
        self,
        *,
        energy: dict,
        mqtt_device_id: str,
        channel_key: str,
    ) -> dict[str, float | None] | None:
        values, rejection_reason = (
            normalize_electrical_measurement(
                energy
            )
        )

        if values is None:
            print(
                "[MQTT] State channel skipped:"
                " measurement contract rejected",
                "| device:",
                mqtt_device_id,
                "| channel:",
                channel_key,
                "| reason:",
                rejection_reason,
            )

            return None

        return values


    def _persist_power_channel_measurement(
        self,
        *,
        mqtt_device_id: str,
        channel_key: str,
        measured_at: datetime,
        values: dict[
            str,
            float | None,
        ],
    ) -> None:
        try:
            power_channel_state = (
                self._get_power_channel_state(
                    mqtt_device_id,
                    channel_key,
                )
            )

        except Exception as exc:
            print(
                "[MQTT] State channel skipped:"
                " power channel cache load failed",
                "| device:",
                mqtt_device_id,
                "| channel:",
                channel_key,
                "| error:",
                type(exc).__name__,
            )

            return

        if power_channel_state is None:
            print(
                "[MQTT] State channel skipped:"
                " channel not found or disabled",
                "| device:",
                mqtt_device_id,
                "| channel:",
                channel_key,
            )

            return

        channel_id = (
            power_channel_state.channel_id
        )

        last_measured_at = (
            power_channel_state
            .last_measured_at
        )

        if last_measured_at is not None:
            elapsed_seconds = (
                measured_at
                - last_measured_at
            ).total_seconds()

            if elapsed_seconds < 0:
                print(
                    "[MQTT] State channel skipped:"
                    " out-of-order measurement",
                    "| device:",
                    mqtt_device_id,
                    "| channel:",
                    channel_key,
                )

                return

            if (
                elapsed_seconds
                < POWER_HISTORY_INTERVAL_SECONDS
            ):
                return

        db = SessionLocal()

        try:
            measurement = PowerMeasurement(
                channel_id=channel_id,

                measured_at=measured_at,

                voltage_v=values[
                    "voltage_v"
                ],

                current_a=values[
                    "current_a"
                ],

                power_w=values[
                    "power_w"
                ],

                energy_kwh=values[
                    "energy_kwh"
                ],

                frequency_hz=values[
                    "frequency_hz"
                ],

                power_factor=values[
                    "power_factor"
                ],
            )

            db.add(
                measurement
            )

            db.commit()

            db.refresh(
                measurement
            )

            self._power_ingestion_cache.record_measurement(
                mqtt_device_id,
                channel_key,
                channel_id,
                measured_at,
            )

            logger.debug(
                "[MQTT] Power measurement stored:"
                " | id: %s | device: %s | channel: %s"
                " | measured_at: %s",
                measurement.id,
                mqtt_device_id,
                channel_key,
                measured_at.isoformat(),
            )

            current_summary_date = (
                local_date_for_timestamp(
                    measured_at
                )
            )

            if (
                self
                ._daily_summary_checked_date
                .get(
                    channel_id
                )
                != current_summary_date
            ):
                try:
                    created_count = (
                        summarize_completed_days(
                            db=db,

                            channel_id=(
                                channel_id
                            ),

                            current_local_date=(
                                current_summary_date
                            ),
                        )
                    )

                    retention_reference_time = (
                        datetime.now(
                            timezone.utc
                        )
                    )

                    retention_local_date = (
                        local_date_for_timestamp(
                            retention_reference_time
                        )
                    )

                    retention_result = (
                        cleanup_channel_history(
                            db=db,

                            channel_id=(
                                channel_id
                            ),

                            reference_time=(
                                retention_reference_time
                            ),

                            current_local_date=(
                                retention_local_date
                            ),
                        )
                    )

                except Exception as exc:
                    db.rollback()

                    print(
                        "[DAILY] Summary check failed:",
                        "| device:",
                        mqtt_device_id,
                        "| channel:",
                        channel_key,
                        "| error:",
                        type(exc).__name__,
                    )

                else:
                    self._daily_summary_checked_date[
                        channel_id
                    ] = current_summary_date

                    print(
                        "[DAILY] Summary check complete:",
                        "| device:",
                        mqtt_device_id,
                        "| channel:",
                        channel_key,
                        "| local_date:",
                        current_summary_date.isoformat(),
                        "| created:",
                        created_count,
                    )

                    print(
                        "[RETENTION] Cleanup complete:",
                        "| device:",
                        mqtt_device_id,
                        "| channel:",
                        channel_key,
                        "| measurements_deleted:",
                        retention_result[
                            "deleted_measurements"
                        ],
                        "| summaries_deleted:",
                        retention_result[
                            "deleted_summaries"
                        ],
                        "| measurement_cutoff:",
                        retention_result[
                            "measurement_cutoff"
                        ].isoformat(),
                        "| summary_cutoff_date:",
                        retention_result[
                            "summary_cutoff_date"
                        ].isoformat(),
                    )

        except IntegrityError as exc:
            db.rollback()

            if (
                getattr(
                    exc.orig,
                    "sqlstate",
                    None,
                )
                == "23505"
            ):
                self._power_ingestion_cache.record_measurement(
                    mqtt_device_id,
                    channel_key,
                    channel_id,
                    measured_at,
                )

                print(
                    "[MQTT] State channel skipped:"
                    " duplicate measurement",
                    "| device:",
                    mqtt_device_id,
                    "| channel:",
                    channel_key,
                    "| measured_at:",
                    measured_at.isoformat(),
                )

                return

            print(
                "[MQTT] State persistence failed:",
                "| device:",
                mqtt_device_id,
                "| channel:",
                channel_key,
                "| error:",
                type(exc).__name__,
            )

        except Exception as exc:
            db.rollback()

            print(
                "[MQTT] State persistence failed:",
                "| device:",
                mqtt_device_id,
                "| channel:",
                channel_key,
                "| error:",
                type(exc).__name__,
            )

            with self._state_lock:
                self._last_error = (
                    "State persistence failed: "
                    f"{type(exc).__name__}"
                )

        finally:
            db.close()


    def _handle_state_message(
        self,
        message,
    ):
        try:
            payload = json.loads(
                message.payload.decode(
                    "utf-8"
                )
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            print(
                "[MQTT] Invalid state payload:",
                message.topic,
            )

            return

        if not isinstance(
            payload,
            dict,
        ):
            print(
                "[MQTT] State skipped:"
                " payload is not an object"
            )

            return

        schema_version = payload.get(
            "schema_version"
        )

        if (
            type(schema_version) is not int
            or schema_version
            not in (
                1,
                2,
            )
        ):
            print(
                "[MQTT] State skipped:"
                " unsupported schema version"
            )

            return

        topic_parts = (
            message.topic.split("/")
        )

        if (
            len(topic_parts) != 3
            or topic_parts[0]
            != "smartmonitor"
            or topic_parts[2]
            != "state"
        ):
            print(
                "[MQTT] State skipped:"
                " invalid topic",
                message.topic,
            )

            return

        mqtt_device_id = topic_parts[1]

        if not mqtt_device_id:
            print(
                "[MQTT] State skipped:"
                " empty MQTT device id"
            )

            return

        payload_device = payload.get(
            "device"
        )

        if (
            payload_device
            != mqtt_device_id
        ):
            print(
                "[MQTT] State skipped:"
                " device/topic mismatch",
                "| topic:",
                mqtt_device_id,
                "| payload:",
                payload_device,
            )

            return

        live_state_store.update(
            mqtt_device_id=mqtt_device_id,
            payload=payload,
        )

        system = payload.get(
            "system"
        )

        if not isinstance(
            system,
            dict,
        ):
            print(
                "[MQTT] State skipped:"
                " missing system block",
                "| device:",
                mqtt_device_id,
            )

            return

        if (
            system.get(
                "ntp_synchronized"
            )
            is not True
        ):
            print(
                "[MQTT] State skipped:"
                " NTP not synchronized",
                "| device:",
                mqtt_device_id,
            )

            return

        timestamp = payload.get(
            "timestamp"
        )

        if (
            not self._is_finite_number(
                timestamp
            )
            or float(timestamp) <= 0
        ):
            print(
                "[MQTT] State skipped:"
                " invalid timestamp",
                "| device:",
                mqtt_device_id,
            )

            return

        try:
            measured_at = (
                datetime.fromtimestamp(
                    float(timestamp),
                    tz=timezone.utc,
                )
            )

        except (
            OverflowError,
            OSError,
            ValueError,
        ):
            print(
                "[MQTT] State skipped:"
                " timestamp out of range",
                "| device:",
                mqtt_device_id,
            )

            return

        managers = payload.get(
            "managers"
        )

        if not isinstance(
            managers,
            dict,
        ):
            print(
                "[MQTT] State skipped:"
                " missing managers block",
                "| device:",
                mqtt_device_id,
            )

            return

        energy_manager = (
            managers.get(
                "energy"
            )
        )

        if not isinstance(
            energy_manager,
            dict,
        ):
            print(
                "[MQTT] State skipped:"
                " missing energy manager",
                "| device:",
                mqtt_device_id,
            )

            return

        energy_status = (
            energy_manager.get(
                "status"
            )
        )

        if energy_status != "OK":
            print(
                "[MQTT] State received with degraded energy manager",
                "| device:",
                mqtt_device_id,
                "| status:",
                energy_status,
            )

        energy_blocks = (
            self._extract_energy_blocks(
                payload=payload,
                schema_version=(
                    schema_version
                ),
                mqtt_device_id=(
                    mqtt_device_id
                ),
            )
        )

        if energy_blocks is None:
            return

        for (
            channel_key,
            energy,
        ) in energy_blocks.items():
            values = (
                self._parse_energy_values(
                    energy=energy,
                    mqtt_device_id=(
                        mqtt_device_id
                    ),
                    channel_key=(
                        channel_key
                    ),
                )
            )

            if values is None:
                continue

            self._persist_power_channel_measurement(
                mqtt_device_id=(
                    mqtt_device_id
                ),
                channel_key=channel_key,
                measured_at=measured_at,
                values=values,
            )


    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ):
        self._connected.clear()

        # Dès que le backend n'est plus relié au
        # broker, les derniers statuts mémorisés
        # ne sont plus considérés fiables.
        with self._device_status_lock:
            self._device_statuses.clear()

        if reason_code != 0:
            with self._state_lock:
                self._last_error = (
                    "Disconnected: "
                    f"{reason_code}"
                )

        print(
            "[MQTT] Backend disconnected:",
            reason_code,
        )

        print(
            "[MQTT] Device statuses cleared"
        )

    def start(self):
        transport = (
            "TLS"
            if MQTT_TLS_ENABLED
            else "plain"
        )

        print(
            f"[MQTT] Connecting to "
            f"{MQTT_HOST}:{MQTT_PORT} "
            f"| transport: {transport}"
        )

        self._start_ingestion_worker()

        self.client.connect_async(
            MQTT_HOST,
            MQTT_PORT,
            MQTT_KEEPALIVE,
        )

        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()
        self._stop_ingestion_worker()

    def send_command(
        self,
        mqtt_device_id: str,
        command: str,
        parameters: dict | None = None,
        timeout: float = 5.0,
    ) -> dict:
        if not self.is_connected():
            raise RuntimeError(
                "MQTT backend is not connected"
            )

        device_status = (
            self.get_device_status(
                mqtt_device_id
            )
        )

        if device_status != "online":
            print(
                "[MQTT] Command rejected:"
                " device unavailable",
                "| device:",
                mqtt_device_id,
                "| status:",
                (
                    device_status
                    if device_status
                    is not None
                    else "unknown"
                ),
            )

            raise DeviceUnavailableError(
                "Device unavailable"
            )

        request_id = str(
            uuid.uuid4()
        )

        command_topic = (
            f"smartmonitor/"
            f"{mqtt_device_id}/command"
        )

        response_topic = (
            f"smartmonitor/"
            f"{mqtt_device_id}/response"
        )

        payload = build_command_payload(
            request_id=request_id,
            command=command,
            parameters=parameters,
        )

        pending = {
            "event":
                threading.Event(),
            "response":
                None,
            "response_topic":
                response_topic,
            "contract_error":
                None,
        }

        with self._pending_lock:
            self._pending_responses[
                request_id
            ] = pending

        try:
            message_info = (
                self.client.publish(
                    command_topic,
                    json.dumps(
                        payload
                    ),
                    qos=0,
                    retain=False,
                )
            )

            if (
                message_info.rc
                != mqtt.MQTT_ERR_SUCCESS
            ):
                raise RuntimeError(
                    "MQTT command publish failed"
                )

            print(
                "[MQTT] Command published:",
                command_topic,
                "| request_id:",
                request_id,
            )

            response_received = (
                pending[
                    "event"
                ].wait(
                    timeout
                )
            )

            if not response_received:
                raise TimeoutError(
                    "Device response timeout"
                )

            contract_error = pending[
                "contract_error"
            ]

            if contract_error is not None:
                raise ValueError(
                    "Invalid device response: "
                    + contract_error
                )

            response = pending[
                "response"
            ]

            if not isinstance(
                response,
                dict,
            ):
                raise ValueError(
                    "Invalid device response"
                )

            if (
                response.get(
                    "request_id"
                )
                != request_id
            ):
                raise ValueError(
                    "Invalid response request_id"
                )

            if response.get(
                "result"
            ) not in (
                "ack",
                "nack",
            ):
                raise ValueError(
                    "Invalid response result"
                )

            if not isinstance(
                response.get(
                    "message"
                ),
                str,
            ):
                raise ValueError(
                    "Invalid response message"
                )

            response_result = (
                response["result"]
            )

            event_data = {
                "request_id": request_id,
                "command": command,
            }

            error_code = response.get(
                "error_code"
            )

            if isinstance(
                error_code,
                str,
            ):
                event_data[
                    "error_code"
                ] = error_code

            self._persist_device_event(
                mqtt_device_id=mqtt_device_id,
                event_type=(
                    "command_ack"
                    if response_result == "ack"
                    else "command_nack"
                ),
                data=event_data,
            )

            return response

        finally:
            with self._pending_lock:
                self._pending_responses.pop(
                    request_id,
                    None,
                )

    def is_connected(
        self,
    ) -> bool:
        return self._connected.is_set()

    def status(
        self,
    ) -> dict:
        with self._state_lock:
            return {
                "connected":
                    self.is_connected(),

                "host":
                    MQTT_HOST,

                "port":
                    MQTT_PORT,

                "tls_enabled":
                    MQTT_TLS_ENABLED,

                "client_id":
                    MQTT_CLIENT_ID,

                "subscriptions":
                    list(
                        MQTT_SUBSCRIPTIONS
                    ),

                "received_messages":
                    self._received_messages,

                "last_message_topic":
                    self._last_message_topic,

                "last_error":
                    self._last_error,
            "ingestion": {
                "queue_depth": self._ingestion_queue.qsize(),
                "dropped_messages": (
                    self._ingestion_dropped_messages
                ),
            },
            }


mqtt_manager = MqttManager()
