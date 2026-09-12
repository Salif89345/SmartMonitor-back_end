from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import threading
import time

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from unittest.mock import patch

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


BACKEND_ROOT = Path(__file__).resolve().parents[1]
for candidate in (BACKEND_ROOT, Path.cwd().resolve()):
    if (candidate / "app").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        BACKEND_ROOT = candidate
        break


def percentile(values: list[float], requested: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * requested / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        raise ValueError("distribution requires at least one value")
    return {
        "samples": len(values),
        "min": round(min(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "max": round(max(values), 3),
    }


def measure_http(url: str, samples: int) -> dict[str, Any]:
    timings = []
    for index in range(samples + 3):
        request = Request(url, headers={"Accept": "application/json"})
        started = time.perf_counter()
        with urlopen(request, timeout=5) as response:
            body = response.read()
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} on {url}: {body[:200]!r}")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if index >= 3:
            timings.append(elapsed_ms)
    return {"url": url, "latency_ms": distribution(timings)}


def measure_database_roundtrip(samples: int) -> dict[str, Any]:
    from app.database import engine

    timings = []
    with engine.connect() as connection:
        for index in range(samples + 3):
            started = time.perf_counter()
            value = connection.execute(text("SELECT 1")).scalar_one()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if value != 1:
                raise RuntimeError("Unexpected SELECT 1 result")
            if index >= 3:
                timings.append(elapsed_ms)
    return {"statement": "SELECT 1", "latency_ms": distribution(timings)}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def measure_observed_mqtt_to_db(samples: int) -> dict[str, Any]:
    from app.database import SessionLocal
    from app.models import Device, DeviceChannel, PowerMeasurement

    with SessionLocal() as db:
        rows = db.execute(
            select(
                PowerMeasurement.measured_at,
                PowerMeasurement.received_at,
                Device.mqtt_device_id,
                DeviceChannel.channel_key,
            )
            .join(DeviceChannel, PowerMeasurement.channel_id == DeviceChannel.id)
            .join(Device, DeviceChannel.device_id == Device.id)
            .order_by(PowerMeasurement.received_at.desc())
            .limit(samples)
        ).all()

    if not rows:
        raise RuntimeError("No power measurements available for MQTT-to-DB baseline")

    latencies = [
        (_as_utc(row.received_at) - _as_utc(row.measured_at)).total_seconds() * 1000.0
        for row in rows
    ]
    newest = rows[0]
    oldest = rows[-1]
    return {
        "definition": "PowerMeasurement.received_at - device measured_at",
        "latency_ms": distribution(latencies),
        "negative_sample_count": sum(value < 0 for value in latencies),
        "newest_received_at": _as_utc(newest.received_at).isoformat(),
        "oldest_received_at": _as_utc(oldest.received_at).isoformat(),
        "devices": sorted({str(row.mqtt_device_id) for row in rows}),
        "channels": sorted({str(row.channel_key) for row in rows}),
    }


@contextmanager
def _sql_counter(engine):
    state: dict[str, Any] = {"count": 0, "durations_ms": [], "started": []}

    def before_cursor_execute(*_args):
        state["started"].append(time.perf_counter())

    def after_cursor_execute(*_args):
        started = state["started"].pop()
        state["count"] += 1
        state["durations_ms"].append((time.perf_counter() - started) * 1000.0)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    try:
        yield state
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)


def measure_controlled_state_sql(samples: int = 30) -> dict[str, Any]:
    from app.models import Base, Device, DeviceChannel
    from app.mqtt_client import MqttManager

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as db:
        device = Device(
            device_uid="SM-OP017-BASELINE",
            mqtt_device_id="op017-baseline",
            name="OP-017 baseline",
            is_active=True,
        )
        db.add(device)
        db.flush()
        channel = DeviceChannel(
            device_id=device.id,
            channel_key="power_1",
            name="Power 1",
            is_enabled=True,
        )
        db.add(channel)
        db.commit()
        channel_id = channel.id

    manager = MqttManager()
    # Une heure fixe en milieu de journée empêche le benchmark synthétique
    # de franchir minuit et de déclencher le traitement quotidien, qui est
    # volontairement exclu du budget SQL de régime permanent.
    measured_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    manager._daily_summary_checked_date[channel_id] = measured_at.date()
    values = {
        "voltage_v": 230.0,
        "current_a": 1.0,
        "power_w": 230.0,
        "energy_kwh": 1.0,
        "frequency_hz": 50.0,
        "power_factor": 1.0,
    }

    def execute(at: datetime) -> tuple[int, float, float]:
        with _sql_counter(engine) as counter:
            started = time.perf_counter()
            manager._persist_power_channel_measurement(
                mqtt_device_id="op017-baseline",
                channel_key="power_1",
                measured_at=at,
                values=values,
            )
            handler_ms = (time.perf_counter() - started) * 1000.0
        return (
            int(counter["count"]),
            handler_ms,
            float(sum(counter["durations_ms"])),
        )

    try:
        with patch("app.mqtt_client.SessionLocal", session_factory):
            cold_count, cold_handler, cold_sql = execute(measured_at)
            warm_count, warm_handler, warm_sql = execute(measured_at + timedelta(seconds=5))
            due_count, due_handler, due_sql = execute(measured_at + timedelta(seconds=61))

            due_counts = []
            due_handlers = []
            due_sql_times = []
            current = measured_at + timedelta(seconds=61)
            for _ in range(samples):
                current += timedelta(seconds=61)
                count, handler_ms, sql_ms = execute(current)
                due_counts.append(count)
                due_handlers.append(handler_ms)
                due_sql_times.append(sql_ms)
    finally:
        engine.dispose()

    expected = {"cold_cache": 4, "warm_cache_not_due": 0, "persistence_due": 2}
    actual = {
        "cold_cache": cold_count,
        "warm_cache_not_due": warm_count,
        "persistence_due": due_count,
    }
    if actual != expected or any(count != 2 for count in due_counts):
        raise RuntimeError(f"SQL budget changed: actual={actual}, expected={expected}")

    return {
        "database": "temporary in-memory SQLite",
        "sql_statements_per_channel": actual,
        "single_samples": {
            "cold_handler_ms": round(cold_handler, 3),
            "cold_sql_ms": round(cold_sql, 3),
            "warm_handler_ms": round(warm_handler, 3),
            "warm_sql_ms": round(warm_sql, 3),
            "due_handler_ms": round(due_handler, 3),
            "due_sql_ms": round(due_sql, 3),
        },
        "persistence_due_handler_ms": distribution(due_handlers),
        "persistence_due_sql_ms": distribution(due_sql_times),
    }


class WindowsProcessSampler:
    def __init__(self, pid: int | None):
        self.pid = pid
        self._samples: list[dict[str, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def start(self) -> None:
        if self.pid is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        if self.pid is None:
            return {"available": False, "reason": "server PID not supplied"}
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._error is not None:
            return {"available": False, "pid": self.pid, "reason": self._error}
        if len(self._samples) < 2:
            return {"available": False, "pid": self.pid, "reason": "insufficient samples"}

        first = self._samples[0]
        last = self._samples[-1]
        elapsed = max(last["wall"] - first["wall"], 0.001)
        cpu_delta = max(last["cpu"] - first["cpu"], 0.0)
        logical_cpus = max(os.cpu_count() or 1, 1)
        working = [sample["working_mb"] for sample in self._samples]
        private = [sample["private_mb"] for sample in self._samples]
        return {
            "available": True,
            "pid": self.pid,
            "samples": len(self._samples),
            "cpu_percent_of_machine": round(cpu_delta / elapsed / logical_cpus * 100.0, 3),
            "working_set_mb": distribution(working),
            "private_memory_mb": distribution(private),
        }

    def _run(self) -> None:
        if platform.system() != "Windows":
            self._error = "external process sampling currently supports Windows"
            return
        try:
            import ctypes
            from ctypes import wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000 | 0x0010, False, self.pid)
            if not handle:
                raise OSError(ctypes.get_last_error(), "OpenProcess failed")

            try:
                while not self._stop.is_set():
                    creation = FILETIME()
                    exit_time = FILETIME()
                    kernel = FILETIME()
                    user = FILETIME()
                    if not kernel32.GetProcessTimes(
                        handle,
                        ctypes.byref(creation),
                        ctypes.byref(exit_time),
                        ctypes.byref(kernel),
                        ctypes.byref(user),
                    ):
                        raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")

                    memory = PROCESS_MEMORY_COUNTERS_EX()
                    memory.cb = ctypes.sizeof(memory)
                    if not psapi.GetProcessMemoryInfo(
                        handle, ctypes.byref(memory), memory.cb
                    ):
                        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")

                    filetime = lambda value: (value.high << 32) | value.low
                    self._samples.append(
                        {
                            "wall": time.perf_counter(),
                            "cpu": (filetime(kernel) + filetime(user)) / 10_000_000.0,
                            "working_mb": memory.WorkingSetSize / 1024.0 / 1024.0,
                            "private_mb": memory.PrivateUsage / 1024.0 / 1024.0,
                        }
                    )
                    self._stop.wait(0.1)
            finally:
                kernel32.CloseHandle(handle)
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"


def build_query_model(channel_count: int, state_interval_seconds: float) -> dict[str, Any]:
    persistence_interval = 60.0
    states_per_persistence = persistence_interval / state_interval_seconds
    before = channel_count * 2.0
    after = channel_count * 2.0 / states_per_persistence
    reduction = (1.0 - after / before) * 100.0
    return {
        "assumptions": {
            "channels": channel_count,
            "state_interval_seconds": state_interval_seconds,
            "persistence_interval_seconds": persistence_interval,
        },
        "pre_op003_sql_statements_per_state": round(before, 3),
        "current_steady_state_sql_statements_per_state": round(after, 3),
        "modeled_reduction_percent": round(reduction, 3),
        "note": "Daily summary and retention checks are excluded from steady state.",
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    api = report["api"]
    process = report["process"]
    process_text = (
        f"{process['cpu_percent_of_machine']} % CPU machine; "
        f"{process['working_set_mb']['p50']} MiB RAM p50"
        if process.get("available")
        else f"indisponible ({process.get('reason')})"
    )
    content = f"""# OP-017 — Baseline de performance backend

Date UTC : {report['generated_at_utc']}

Résultat : **{report['result']}**

## Mesures

| Indicateur | p50 | p95 | Échantillons |
|---|---:|---:|---:|
| API `/api/v1/health` (ms) | {api['health']['latency_ms']['p50']} | {api['health']['latency_ms']['p95']} | {api['health']['latency_ms']['samples']} |
| API `/api/v1/health/database` (ms) | {api['database_health']['latency_ms']['p50']} | {api['database_health']['latency_ms']['p95']} | {api['database_health']['latency_ms']['samples']} |
| SQL direct `SELECT 1` (ms) | {report['database_roundtrip']['latency_ms']['p50']} | {report['database_roundtrip']['latency_ms']['p95']} | {report['database_roundtrip']['latency_ms']['samples']} |
| ESP/MQTT vers DB observé (ms) | {report['observed_mqtt_to_db']['latency_ms']['p50']} | {report['observed_mqtt_to_db']['latency_ms']['p95']} | {report['observed_mqtt_to_db']['latency_ms']['samples']} |
| Écriture state contrôlée (ms) | {report['controlled_state_sql']['persistence_due_handler_ms']['p50']} | {report['controlled_state_sql']['persistence_due_handler_ms']['p95']} | {report['controlled_state_sql']['persistence_due_handler_ms']['samples']} |

Processus backend : {process_text}.

## Budget SQL par canal et par state

- Cache froid au premier message : {report['controlled_state_sql']['sql_statements_per_channel']['cold_cache']} instructions SQL.
- Cache chaud, persistance non due : {report['controlled_state_sql']['sql_statements_per_channel']['warm_cache_not_due']} instruction SQL.
- Persistance due : {report['controlled_state_sql']['sql_statements_per_channel']['persistence_due']} instructions SQL.
- Avant OP-003, modèle à {report['query_model']['assumptions']['channels']} canaux : {report['query_model']['pre_op003_sql_statements_per_state']} instructions/state.
- Après OP-003, régime permanent modélisé : {report['query_model']['current_steady_state_sql_statements_per_state']} instruction/state, soit {report['query_model']['modeled_reduction_percent']} % de réduction.

## Limites

- Le délai ESP/MQTT vers DB est calculé par `received_at - measured_at` et dépend donc de la qualité de l'horloge de l'ESP.
- Le comptage SQL contrôlé exécute le vrai chemin d'ingestion avec SQLite en mémoire ; les latences PostgreSQL réelles sont mesurées séparément.
- Cette baseline locale n'est pas un test de charge, d'endurance ou de certification.
- Les seuils d'acceptation produit devront être fixés après comparaison avec une seconde baseline représentative de la cible d'hébergement.
"""
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mesure la baseline OP-017.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--api-samples", type=int, default=50)
    parser.add_argument("--db-samples", type=int, default=50)
    parser.add_argument("--ingestion-samples", type=int, default=40)
    parser.add_argument("--controlled-samples", type=int, default=30)
    parser.add_argument("--channel-count", type=int, default=2)
    parser.add_argument("--state-interval-seconds", type=float, default=5.0)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/op017"))
    args = parser.parse_args()

    if min(args.api_samples, args.db_samples, args.ingestion_samples, args.controlled_samples) < 5:
        parser.error("each sample count must be at least 5")
    if args.channel_count < 1 or args.state_interval_seconds <= 0:
        parser.error("invalid channel count or state interval")

    sampler = WindowsProcessSampler(args.server_pid)
    sampler.start()
    try:
        print("[OP-017] Mesure des latences API...", flush=True)
        api = {
            "health": measure_http(f"{args.base_url.rstrip('/')}/api/v1/health", args.api_samples),
            "database_health": measure_http(
                f"{args.base_url.rstrip('/')}/api/v1/health/database", args.api_samples
            ),
        }
        print("[OP-017] Mesure du temps SQL PostgreSQL...", flush=True)
        database_roundtrip = measure_database_roundtrip(args.db_samples)
        print("[OP-017] Lecture des delais MQTT vers DB...", flush=True)
        observed = measure_observed_mqtt_to_db(args.ingestion_samples)
        print("[OP-017] Controle du budget SQL par state...", flush=True)
        controlled = measure_controlled_state_sql(args.controlled_samples)
        time.sleep(0.25)
    finally:
        process = sampler.stop()

    if not process.get("available"):
        raise RuntimeError(f"Backend process metrics unavailable: {process.get('reason')}")

    generated_at = datetime.now(timezone.utc)
    report = {
        "op": "OP-017",
        "result": "PASS",
        "generated_at_utc": generated_at.isoformat(),
        "python": platform.python_version(),
        "api": api,
        "database_roundtrip": database_roundtrip,
        "observed_mqtt_to_db": observed,
        "controlled_state_sql": controlled,
        "query_model": build_query_model(args.channel_count, args.state_interval_seconds),
        "process": process,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    json_path = args.output_dir / f"op017_baseline_{stamp}.json"
    markdown_path = args.output_dir / f"op017_baseline_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(report, markdown_path)

    print(f"[OP-017] API health p50/p95 : {api['health']['latency_ms']['p50']} / {api['health']['latency_ms']['p95']} ms")
    print(f"[OP-017] API DB p50/p95     : {api['database_health']['latency_ms']['p50']} / {api['database_health']['latency_ms']['p95']} ms")
    print(f"[OP-017] MQTT->DB p50/p95   : {observed['latency_ms']['p50']} / {observed['latency_ms']['p95']} ms")
    print(f"[OP-017] SQL/state actuel   : {report['query_model']['current_steady_state_sql_statements_per_state']}")
    print(f"[OP-017] CPU / RAM backend  : {process['cpu_percent_of_machine']} % / {process['working_set_mb']['p50']} MiB")
    print(f"[OP-017] Rapport JSON       : {json_path.resolve()}")
    print(f"[OP-017] Rapport Markdown   : {markdown_path.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[OP-017] ECHEC : {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
