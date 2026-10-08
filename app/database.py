import os
from pathlib import Path

from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.settings import (
    APP_ENV,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    SM015_TRANSFER_STAGING_ENABLED,
)


_bench_sqlite_path = os.getenv("SM015_BENCH_SQLITE_PATH")
if _bench_sqlite_path:
    # Never let a bench setting silently redirect the production backend.
    if APP_ENV != "dev" or not SM015_TRANSFER_STAGING_ENABLED:
        raise RuntimeError("SM015 bench database requires enabled development staging")
    _bench_path = Path(_bench_sqlite_path)
    if not _bench_path.is_absolute() or _bench_path.suffix != ".sqlite3":
        raise RuntimeError("SM015 bench database must be an absolute .sqlite3 path")
    DATABASE_URL = URL.create("sqlite+pysqlite", database=str(_bench_path))
    _engine_options = {"connect_args": {"check_same_thread": False}}
else:
    DATABASE_URL = URL.create(
        "postgresql+psycopg",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
    )
    _engine_options = {"pool_pre_ping": True}


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    **_engine_options,
)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()


def check_database_connection():
    with engine.connect() as connection:
        if _bench_sqlite_path:
            connection.execute(text("SELECT 1")).one()
            return {"database": "sm015-bench", "user": "bench-local"}
        result = connection.execute(
            text("SELECT current_database(), current_user")
        ).one()

        return {
            "database": result[0],
            "user": result[1],
        }
