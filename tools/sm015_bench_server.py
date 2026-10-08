"""Run an isolated, time-limited SM-015 phone/ESP32 transfer rehearsal.

This launcher never writes the normal database. It accepts only the known USB
bench board, gives it a temporary factory test key, and erases that key on
normal exit. It cannot complete an ownership transfer without broker evidence.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
from threading import Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


BENCH_UID = "SM-24367DBD2A58"
BENCH_MQTT_ID = "sm015-bench"
BENCH_PORT = 8001
MAX_RUNTIME_SECONDS = 45 * 60


def _serial_helpers():
    # Borrow the already installed pyserial from PlatformIO; install nothing.
    sys.path.append(str(Path.home() / ".platformio" / "penv" / "Lib" / "site-packages"))
    import serial

    firmware_tools = Path(__file__).resolve().parents[2] / "SmartMonitor" / "tools"
    if not (firmware_tools / "sm015_bench_provision.py").is_file():
        raise RuntimeError("Outils USB du banc introuvables")
    sys.path.insert(0, str(firmware_tools))
    from sm015_bench_provision import verify_bench
    from sm015_bench_reset_proof import clear_key, command, generation

    return serial, verify_bench, clear_key, command, generation


def _arm_board(port_name: str, factory_key: bytearray) -> int:
    serial, verify_bench, clear_key, command, generation = _serial_helpers()
    with serial.Serial(port_name, 115200, timeout=0.5, write_timeout=3) as port:
        verify_bench(port)  # Checks exact bench UID before sending any secret.
        before = generation(port)
        clear_key(port)
        command(
            port,
            "SM015_FACTORY_KEY=" + factory_key.hex().upper(),
            "SM015_BENCH_FACTORY_KEY_SAVED",
        )
    return before


def _clear_board(port_name: str) -> None:
    serial, verify_bench, clear_key, _, _ = _serial_helpers()
    with serial.Serial(port_name, 115200, timeout=0.5, write_timeout=3) as port:
        verify_bench(port)
        clear_key(port)


def _seed_database(owner_password: str, buyer_password: str) -> None:
    from app.database import Base, SessionLocal, engine
    from app.models import Device, DeviceMembership, User
    from app.security import hash_password

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        owner = User(
            email="ancien@sm015.example.com",
            password_hash=hash_password(owner_password),
            email_verified=True,
        )
        buyer = User(
            email="nouveau@sm015.example.com",
            password_hash=hash_password(buyer_password),
            email_verified=True,
        )
        device = Device(
            device_uid=BENCH_UID,
            mqtt_device_id=BENCH_MQTT_ID,
            name="SmartMonitor de banc",
        )
        db.add_all((owner, buyer, device))
        db.flush()
        db.add(DeviceMembership(user_id=owner.id, device_id=device.id, role="owner"))
        db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3", help="port du second ESP32 de banc")
    args = parser.parse_args()

    # All configuration is set before importing backend modules. The bench key
    # and passwords are random per run and never accepted from HTTP or argv.
    key = bytearray(secrets.token_bytes(32))
    old_password = secrets.token_urlsafe(12)
    new_password = secrets.token_urlsafe(12)
    armed = False
    with TemporaryDirectory(prefix="sm015-bench-") as directory:
        os.environ.update(
            APP_ENV="dev",
            SM015_TRANSFER_STAGING_ENABLED="true",
            SM015_BENCH_SQLITE_PATH=str(Path(directory) / "bench.sqlite3"),
            SM015_BENCH_DEVICE_UID=BENCH_UID,
            SM015_BENCH_FACTORY_KEY_HEX=key.hex().upper(),
            MQTT_CLIENT_ID="smartmonitor-backend-sm015-" + secrets.token_hex(5),
            JWT_SECRET_KEY=secrets.token_hex(32),
            EMAIL_VERIFICATION_SECRET=secrets.token_hex(32),
            API_ALLOWED_HOSTS="127.0.0.1,localhost",
        )
        try:
            # Fail before arming the board if the backend cannot be prepared.
            _seed_database(old_password, new_password)
            from app.main import app
            import uvicorn

            before = _arm_board(args.port, key)
            armed = True
            server = uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=BENCH_PORT, log_level="warning")
            )
            timer = Timer(MAX_RUNTIME_SECONDS, lambda: setattr(server, "should_exit", True))
            timer.daemon = True
            timer.start()
            print("SM015_BENCH_READY port=8001 uid=" + BENCH_UID, flush=True)
            print("Génération avant reset physique : " + str(before), flush=True)
            print("Compte ancien : ancien@sm015.example.com / " + old_password, flush=True)
            print("Compte nouveau : nouveau@sm015.example.com / " + new_password, flush=True)
            print("Arrêt automatique après 45 minutes ; aucune production modifiée.", flush=True)
            try:
                server.run()
            finally:
                timer.cancel()
        finally:
            if armed:
                try:
                    _clear_board(args.port)
                    print("Clé temporaire du second ESP32 effacée.", flush=True)
                except Exception as error:
                    print(
                        "ATTENTION : effacement de la clé de banc non confirmé : "
                        + str(error)
                        + ". Relancer sm015_bench_reset_proof.py --cleanup --port "
                        + args.port,
                        file=sys.stderr,
                        flush=True,
                    )
            key[:] = b"\x00" * len(key)
            os.environ.pop("SM015_BENCH_FACTORY_KEY_HEX", None)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError, ValueError, KeyboardInterrupt) as error:
        print("SM015_BENCH_ERROR " + str(error), file=sys.stderr)
        raise SystemExit(1)
