from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import paho.mqtt.client as mqtt


# ---------------------------------------------------------------------
# Permet d'importer app.settings mÃªme lorsque le script est lancÃ©
# depuis tools/.
# ---------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from app.settings import (  # noqa: E402
    MQTT_HOST,
    MQTT_KEEPALIVE,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_TLS_ENABLED,
    MQTT_CA_CERT_PATH,
    MQTT_USERNAME,
)


TOPIC_FILTER = "smartmonitor/+/state"

latest_samples: dict[str, dict] = {}
samples_lock = Lock()


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def on_connect(
    client,
    userdata,
    flags,
    reason_code,
    properties,
):
    if reason_code != 0:
        print(
            "[OP-008] MQTT connection failed:",
            reason_code,
        )
        return

    print(
        "[OP-008] MQTT connected:",
        f"{MQTT_HOST}:{MQTT_PORT}",
    )

    client.subscribe(
        TOPIC_FILTER
    )

    print(
        "[OP-008] Subscribed:",
        TOPIC_FILTER,
    )


def on_message(
    client,
    userdata,
    message,
):
    try:
        payload = json.loads(
            message.payload.decode("utf-8")
        )
    except Exception:
        return

    system = payload.get("system")

    if not isinstance(system, dict):
        return

    required = (
        "free_heap_bytes",
        "min_free_heap_bytes",
        "largest_free_block_bytes",
    )

    if any(
        key not in system
        for key in required
    ):
        return

    topic_parts = message.topic.split("/")

    if len(topic_parts) >= 3:
        mqtt_device_id = topic_parts[-2]
    else:
        mqtt_device_id = "unknown"

    sample = {
        "received_at_utc": utc_now_iso(),
        "mqtt_device_id": mqtt_device_id,
        "topic": message.topic,
        "boot_count": system.get("boot_count"),
        "reset_reason": system.get(
            "reset_reason"
        ),
        "rssi_dbm": system.get("rssi_dbm"),
        "free_heap_bytes": int(
            system["free_heap_bytes"]
        ),
        "min_free_heap_bytes": int(
            system["min_free_heap_bytes"]
        ),
        "largest_free_block_bytes": int(
            system[
                "largest_free_block_bytes"
            ]
        ),
    }

    with samples_lock:
        latest_samples[
            mqtt_device_id
        ] = sample


def percent_drop(
    baseline: int,
    current: int,
) -> float:
    if baseline <= 0:
        return 0.0

    return (
        (baseline - current)
        / baseline
        * 100.0
    )


def classify_sample(
    baseline: dict,
    sample: dict,
) -> tuple[float, float, float, float, str]:

    free_heap = sample[
        "free_heap_bytes"
    ]

    min_heap = sample[
        "min_free_heap_bytes"
    ]

    largest = sample[
        "largest_free_block_bytes"
    ]

    fragmentation_pct = 0.0

    if free_heap > 0:
        fragmentation_pct = (
            1.0
            - (
                largest
                / free_heap
            )
        ) * 100.0

    free_drop = percent_drop(
        baseline["free_heap_bytes"],
        free_heap,
    )

    min_drop = percent_drop(
        baseline["min_free_heap_bytes"],
        min_heap,
    )

    largest_drop = percent_drop(
        baseline[
            "largest_free_block_bytes"
        ],
        largest,
    )

    baseline_fragmentation = (
        baseline[
            "fragmentation_pct"
        ]
    )

    fragmentation_growth = (
        fragmentation_pct
        - baseline_fragmentation
    )

    alert = "OK"

    if (
        free_drop >= 40.0
        or min_drop >= 40.0
        or largest_drop >= 40.0
        or fragmentation_growth >= 30.0
    ):
        alert = "CRITICAL"

    elif (
        free_drop >= 20.0
        or min_drop >= 20.0
        or largest_drop >= 25.0
        or fragmentation_growth >= 15.0
    ):
        alert = "WARNING"

    return (
        fragmentation_pct,
        free_drop,
        min_drop,
        largest_drop,
        alert,
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--hours",
        type=float,
        default=72.0,
    )

    parser.add_argument(
        "--minutes",
        type=float,
        default=None,
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=60,
    )

    args = parser.parse_args()

    if args.minutes is not None:
        duration_seconds = (
            args.minutes * 60.0
        )
    else:
        duration_seconds = (
            args.hours * 3600.0
        )

    reports_dir = (
        PROJECT_ROOT
        / "reports"
        / "op008"
    )

    reports_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    csv_path = (
        reports_dir
        / f"op008_heap_{timestamp}.csv"
    )

    client_id = (
        "smartmonitor-op008-"
        + socket.gethostname()
        + "-"
        + str(os.getpid())
    )

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )

    if MQTT_USERNAME:
        client.username_pw_set(
            MQTT_USERNAME,
            MQTT_PASSWORD,
        )

    if MQTT_TLS_ENABLED:
        client.tls_set(
            ca_certs=(
                MQTT_CA_CERT_PATH
            )
        )

    client.on_connect = on_connect
    client.on_message = on_message

    print(
        "[OP-008] Starting heap endurance logger"
    )

    print(
        "[OP-008] Client ID:",
        client_id,
    )

    print(
        "[OP-008] Output:",
        csv_path,
    )

    print(
        "[OP-008] Duration:",
        round(
            duration_seconds
            / 3600.0,
            3,
        ),
        "hours",
    )

    print(
        "[OP-008] Interval:",
        args.interval,
        "seconds",
    )

    client.connect(
        MQTT_HOST,
        MQTT_PORT,
        MQTT_KEEPALIVE,
    )

    client.loop_start()

    baselines: dict[str, dict] = {}

    fieldnames = [
        "logged_at_utc",
        "received_at_utc",
        "mqtt_device_id",
        "topic",
        "boot_count",
        "reset_reason",
        "rssi_dbm",
        "free_heap_bytes",
        "min_free_heap_bytes",
        "largest_free_block_bytes",
        "fragmentation_pct",
        "free_drop_pct",
        "min_drop_pct",
        "largest_drop_pct",
        "alert",
    ]

    start_time = time.monotonic()

    next_sample_time = start_time

    announced_checkpoints = set()

    try:
        with csv_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as csv_file:

            writer = csv.DictWriter(
                csv_file,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            csv_file.flush()

            while True:
                now = time.monotonic()

                elapsed = (
                    now - start_time
                )

                if elapsed >= duration_seconds:
                    break

                for checkpoint in (
                    24,
                    48,
                    72,
                ):
                    if (
                        elapsed
                        >= checkpoint * 3600
                        and checkpoint
                        not in announced_checkpoints
                    ):
                        announced_checkpoints.add(
                            checkpoint
                        )

                        print(
                            f"[OP-008] CHECKPOINT "
                            f"{checkpoint} h reached"
                        )

                if now < next_sample_time:
                    time.sleep(
                        min(
                            1.0,
                            next_sample_time
                            - now,
                        )
                    )
                    continue

                next_sample_time = (
                    now + args.interval
                )

                with samples_lock:
                    samples = dict(
                        latest_samples
                    )

                if not samples:
                    print(
                        "[OP-008] Waiting for "
                        "valid state telemetry..."
                    )
                    continue

                for (
                    mqtt_device_id,
                    sample,
                ) in samples.items():

                    if (
                        mqtt_device_id
                        not in baselines
                    ):
                        free_heap = sample[
                            "free_heap_bytes"
                        ]

                        largest = sample[
                            "largest_free_block_bytes"
                        ]

                        fragmentation = 0.0

                        if free_heap > 0:
                            fragmentation = (
                                1.0
                                - largest
                                / free_heap
                            ) * 100.0

                        baselines[
                            mqtt_device_id
                        ] = {
                            "free_heap_bytes":
                                free_heap,

                            "min_free_heap_bytes":
                                sample[
                                    "min_free_heap_bytes"
                                ],

                            "largest_free_block_bytes":
                                largest,

                            "fragmentation_pct":
                                fragmentation,
                        }

                        print(
                            "[OP-008] Baseline:",
                            mqtt_device_id,
                            "| free:",
                            free_heap,
                            "| min:",
                            sample[
                                "min_free_heap_bytes"
                            ],
                            "| largest:",
                            largest,
                        )

                    (
                        fragmentation,
                        free_drop,
                        min_drop,
                        largest_drop,
                        alert,
                    ) = classify_sample(
                        baselines[
                            mqtt_device_id
                        ],
                        sample,
                    )

                    row = {
                        "logged_at_utc":
                            utc_now_iso(),

                        **sample,

                        "fragmentation_pct":
                            round(
                                fragmentation,
                                2,
                            ),

                        "free_drop_pct":
                            round(
                                free_drop,
                                2,
                            ),

                        "min_drop_pct":
                            round(
                                min_drop,
                                2,
                            ),

                        "largest_drop_pct":
                            round(
                                largest_drop,
                                2,
                            ),

                        "alert":
                            alert,
                    }

                    writer.writerow(row)

                    csv_file.flush()

                    print(
                        "[OP-008]",
                        mqtt_device_id,
                        "| free:",
                        sample[
                            "free_heap_bytes"
                        ],
                        "| min:",
                        sample[
                            "min_free_heap_bytes"
                        ],
                        "| largest:",
                        sample[
                            "largest_free_block_bytes"
                        ],
                        "| frag:",
                        f"{fragmentation:.1f}%",
                        "|",
                        alert,
                    )

    except KeyboardInterrupt:
        print(
            "\n[OP-008] Interrupted by user"
        )

    finally:
        client.disconnect()
        client.loop_stop()

    print(
        "[OP-008] Logger stopped"
    )

    print(
        "[OP-008] Results:",
        csv_path,
    )


if __name__ == "__main__":
    main()
