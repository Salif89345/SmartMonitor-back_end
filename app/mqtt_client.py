import hashlib
import hmac
import json
import math
import threading
import time
import uuid

from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from sqlalchemy import select

from app.database import SessionLocal
from app.device_events import create_device_event_by_mqtt_id
from app.settings import (
    MQTT_CLIENT_ID,
    MQTT_HOST,
    MQTT_KEEPALIVE,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_USERNAME,
    POWER_HISTORY_INTERVAL_SECONDS,
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
    "smartmonitor/+/response",
    "smartmonitor/provisioning/+/claim-proof",
)

CLAIM_PROOF_TTL_SECONDS = 120.0


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

        self._claim_proof_lock = (
            threading.Lock()
        )

        self._claim_proofs: dict[
            str,
            dict,
        ] = {}

        self._consumed_claim_proofs: dict[
            tuple[str, str],
            float,
        ] = {}

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

            print(
                "[MQTT] Connection failed:",
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

        print("[MQTT] Backend connected")

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
                print(
                    "[MQTT] Subscription requested:",
                    topic,
                )

            else:
                print(
                    "[MQTT] Subscription request failed:",
                    topic,
                    "| code:",
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
            print(
                "[MQTT] Subscription refused:",
                failures,
            )

            with self._state_lock:
                self._last_error = (
                    "Subscription refused: "
                    f"{failures}"
                )

            return

        print(
            "[MQTT] Subscription confirmed"
        )

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

        print(
            "[MQTT] Message received:",
            message.topic,
            "| bytes:",
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

        if message.topic.endswith(
            "/state"
        ):
            self._handle_state_message(
                message
            )

            return

        if message.topic.endswith(
            "/status"
        ):
            self._handle_status_message(
                message
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

            pending[
                "response"
            ] = payload

            pending[
                "event"
            ].set()

    def _prune_claim_proofs(
        self,
        now: float,
    ) -> None:
        expired_device_uids = [
            device_uid
            for device_uid, proof
            in self._claim_proofs.items()
            if proof["expires_at"] <= now
        ]

        for device_uid in expired_device_uids:
            self._claim_proofs.pop(
                device_uid,
                None,
            )

        expired_consumed_proofs = [
            key
            for key, expires_at
            in self._consumed_claim_proofs.items()
            if expires_at <= now
        ]

        for key in expired_consumed_proofs:
            self._consumed_claim_proofs.pop(
                key,
                None,
            )

    def _handle_claim_proof_message(
        self,
        message,
    ) -> None:
        topic_parts = (
            message.topic.split("/")
        )

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
                message.payload.decode(
                    "utf-8"
                )
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

        if not isinstance(
            payload,
            dict,
        ):
            return

        device_uid = payload.get(
            "device_uid"
        )

        proof_sha256 = payload.get(
            "proof_sha256"
        )

        mqtt_device_id = payload.get(
            "mqtt_device_id"
        )

        if (
            not isinstance(
                device_uid,
                str,
            )
            or len(device_uid) != 15
            or not device_uid.startswith(
                "SM-"
            )
            or any(
                character
                not in "0123456789ABCDEF"
                for character
                in device_uid[3:]
            )
            or device_uid
            != topic_device_uid
        ):
            print(
                "[MQTT] Invalid claim device UID:",
                message.topic,
            )

            return

        if (
            not isinstance(
                proof_sha256,
                str,
            )
            or len(proof_sha256) != 64
            or any(
                character
                not in "0123456789ABCDEF"
                for character
                in proof_sha256
            )
        ):
            print(
                "[MQTT] Invalid claim proof digest:",
                device_uid,
            )

            return

        if (
            not isinstance(
                mqtt_device_id,
                str,
            )
            or not mqtt_device_id
            or len(mqtt_device_id) > 31
            or any(
                character in "/+#"
                for character
                in mqtt_device_id
            )
        ):
            print(
                "[MQTT] Invalid claim MQTT identity:",
                device_uid,
            )

            return

        now = time.monotonic()

        with self._claim_proof_lock:
            self._prune_claim_proofs(
                now
            )

            normalized_digest = (
                proof_sha256.lower()
            )

            consumed_key = (
                device_uid,
                normalized_digest,
            )

            if consumed_key in (
                self._consumed_claim_proofs
            ):
                return

            existing_proof = (
                self._claim_proofs.get(
                    device_uid
                )
            )

            if (
                existing_proof is not None
                and existing_proof.get(
                    "reservation_id"
                ) is not None
                and existing_proof[
                    "proof_sha256"
                ] == normalized_digest
            ):
                return

            self._claim_proofs[
                device_uid
            ] = {
                "proof_sha256":
                    normalized_digest,

                "mqtt_device_id":
                    mqtt_device_id,

                "expires_at":
                    now
                    + CLAIM_PROOF_TTL_SECONDS,

                "reservation_id": None,

                "reservation_expires_at":
                    0.0,
            }

        # Le nonce lui-même ne transite jamais
        # par MQTT et n'est jamais journalisé.
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
        if (
            len(nonce) != 32
            or any(
                character
                not in "0123456789ABCDEF"
                for character
                in nonce
            )
        ):
            return None

        calculated_sha256 = (
            hashlib.sha256(
                nonce.encode(
                    "ascii"
                )
            )
            .hexdigest()
        )

        now = time.monotonic()

        with self._claim_proof_lock:
            self._prune_claim_proofs(
                now
            )

            proof = self._claim_proofs.get(
                device_uid
            )

            if proof is None:
                return None

            if not hmac.compare_digest(
                calculated_sha256,
                proof["proof_sha256"],
            ):
                return None

            reservation_id = proof.get(
                "reservation_id"
            )

            reservation_expires_at = (
                proof.get(
                    "reservation_expires_at",
                    0.0,
                )
            )

            if (
                reservation_id is not None
                and reservation_expires_at > now
            ):
                return None

            reservation_id = str(
                uuid.uuid4()
            )

            proof["reservation_id"] = (
                reservation_id
            )

            proof[
                "reservation_expires_at"
            ] = proof["expires_at"]

            mqtt_device_id = proof[
                "mqtt_device_id"
            ]

        return (
            reservation_id,
            mqtt_device_id,
        )

    def release_claim_proof(
        self,
        *,
        device_uid: str,
        reservation_id: str,
    ) -> None:
        with self._claim_proof_lock:
            proof = self._claim_proofs.get(
                device_uid
            )

            if (
                proof is None
                or proof.get("reservation_id")
                != reservation_id
            ):
                return

            proof["reservation_id"] = None
            proof[
                "reservation_expires_at"
            ] = 0.0

    def commit_claim_proof(
        self,
        *,
        device_uid: str,
        reservation_id: str,
    ) -> bool:
        now = time.monotonic()

        with self._claim_proof_lock:
            self._prune_claim_proofs(
                now
            )

            proof = self._claim_proofs.get(
                device_uid
            )

            if (
                proof is None
                or proof.get("reservation_id")
                != reservation_id
            ):
                return False

            digest = proof["proof_sha256"]

            self._claim_proofs.pop(
                device_uid,
                None,
            )

            self._consumed_claim_proofs[
                (device_uid, digest)
            ] = (
                now
                + CLAIM_PROOF_TTL_SECONDS
            )

        return True


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

        if (
            payload.get(
                "schema_version"
            )
            != 1
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
                "[MQTT] State skipped:"
                " energy manager not OK",
                "| device:",
                mqtt_device_id,
                "| status:",
                energy_status,
            )

            return

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

            return

        field_names = (
            "voltage_v",
            "current_a",
            "power_w",
            "energy_kwh",
            "frequency_hz",
            "power_factor",
        )

        values: dict[
            str,
            float | None,
        ] = {}

        for field_name in field_names:
            value = energy.get(
                field_name
            )

            if value is None:
                values[
                    field_name
                ] = None

                continue

            if not self._is_finite_number(
                value
            ):
                print(
                    "[MQTT] State skipped:"
                    " invalid energy value",
                    "| device:",
                    mqtt_device_id,
                    "| field:",
                    field_name,
                )

                return

            values[
                field_name
            ] = float(value)

        required_fields = (
            "voltage_v",
            "current_a",
            "power_w",
        )

        missing_required = [
            field_name
            for field_name
            in required_fields
            if values[
                field_name
            ] is None
        ]

        if missing_required:
            print(
                "[MQTT] State skipped:"
                " incomplete core measurement",
                "| device:",
                mqtt_device_id,
                "| missing:",
                ",".join(
                    missing_required
                ),
            )

            return

        db = SessionLocal()

        try:
            channel = db.scalar(
                select(
                    DeviceChannel
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
                    == "power_1",

                    DeviceChannel.is_enabled.is_(
                        True
                    ),
                )
            )

            if channel is None:
                print(
                    "[MQTT] State skipped:"
                    " channel not found",
                    "| device:",
                    mqtt_device_id,
                    "| channel: power_1",
                )

                return

            last_measured_at = db.scalar(
                select(
                    PowerMeasurement.measured_at
                )
                .where(
                    PowerMeasurement.channel_id
                    == channel.id,
                )
                .order_by(
                    PowerMeasurement
                    .measured_at
                    .desc()
                )
                .limit(1)
            )

            if (
                last_measured_at
                is not None
            ):
                elapsed_seconds = (
                    measured_at
                    - last_measured_at
                ).total_seconds()

                if elapsed_seconds < 0:
                    print(
                        "[MQTT] State skipped:"
                        " out-of-order measurement",
                        "| device:",
                        mqtt_device_id,
                        "| channel:",
                        channel.channel_key,
                    )

                    return

                if (
                    elapsed_seconds
                    < POWER_HISTORY_INTERVAL_SECONDS
                ):
                    return

            existing_id = db.scalar(
                select(
                    PowerMeasurement.id
                )
                .where(
                    PowerMeasurement.channel_id
                    == channel.id,

                    PowerMeasurement.measured_at
                    == measured_at,
                )
                .limit(1)
            )

            if existing_id is not None:
                print(
                    "[MQTT] State skipped:"
                    " duplicate measurement",
                    "| device:",
                    mqtt_device_id,
                    "| channel:",
                    channel.channel_key,
                    "| measured_at:",
                    measured_at.isoformat(),
                )

                return

            measurement = (
                PowerMeasurement(
                    channel_id=channel.id,

                    measured_at=(
                        measured_at
                    ),

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
            )

            db.add(
                measurement
            )

            db.commit()

            db.refresh(
                measurement
            )

            print(
                "[MQTT] Power measurement stored:",
                "| id:",
                measurement.id,
                "| device:",
                mqtt_device_id,
                "| channel:",
                channel.channel_key,
                "| measured_at:",
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
                    channel.id
                )
                != current_summary_date
            ):
                try:
                    created_count = (
                        summarize_completed_days(
                            db=db,

                            channel_id=(
                                channel.id
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
                                channel.id
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
                        channel.channel_key,
                        "| error:",
                        type(
                            exc
                        ).__name__,
                    )

                else:
                    self._daily_summary_checked_date[
                        channel.id
                    ] = current_summary_date

                    print(
                        "[DAILY] Summary check complete:",
                        "| device:",
                        mqtt_device_id,
                        "| channel:",
                        channel.channel_key,
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
                        channel.channel_key,
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

        except Exception as exc:
            db.rollback()

            print(
                "[MQTT] State persistence failed:",
                "| device:",
                mqtt_device_id,
                "| error:",
                type(
                    exc
                ).__name__,
            )

            with self._state_lock:
                self._last_error = (
                    "State persistence failed: "
                    f"{type(exc).__name__}"
                )

        finally:
            db.close()

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
        print(
            f"[MQTT] Connecting to "
            f"{MQTT_HOST}:{MQTT_PORT}"
        )

        self.client.connect_async(
            MQTT_HOST,
            MQTT_PORT,
            MQTT_KEEPALIVE,
        )

        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

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

        payload = {
            "schema_version": 1,
            "request_id": request_id,
            "command": command,
            "parameters":
                parameters or {},
        }

        pending = {
            "event":
                threading.Event(),
            "response":
                None,
            "response_topic":
                response_topic,
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
            }


mqtt_manager = MqttManager()
