from contextlib import asynccontextmanager
from app.mqtt_client import mqtt_manager
from fastapi import FastAPI, HTTPException

from app.auth import router as auth_router
from app.database import check_database_connection
from app.devices import router as devices_router
from app.commands import router as commands_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_manager.start()

    yield

    mqtt_manager.stop()


app = FastAPI(
    title="SmartMonitor Backend API",
    lifespan=lifespan,
    description="Backend API for SmartMonitor V1",
    version="0.1.0",
)


app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(commands_router)


@app.get("/")
def root():
    return {
        "service": "SmartMonitor Backend",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
    }


@app.get("/api/v1/health/database")
def database_health():
    try:
        database_info = check_database_connection()

        return {
            "status": "ok",
            "database": database_info["database"],
            "user": database_info["user"],
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )

@app.get("/api/v1/health/mqtt")
def health_mqtt():
    status = mqtt_manager.status()

    if not status["connected"]:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "mqtt": status,
            },
        )

    return {
        "status": "ok",
        "mqtt": status,
    }