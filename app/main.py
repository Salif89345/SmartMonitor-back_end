import logging
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import (
    TrustedHostMiddleware,
)
from fastapi.responses import JSONResponse

from app.auth import router as auth_router
from app.commands import router as commands_router
from app.database import check_database_connection
from app.devices import router as devices_router
from app.mqtt_client import mqtt_manager
from app.settings import (
    API_ALLOWED_HOSTS,
    APP_ENV,
    CORS_ALLOWED_ORIGINS,
)


logger = logging.getLogger(
    "uvicorn.error"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_manager.start()

    yield

    mqtt_manager.stop()


docs_enabled = APP_ENV == "dev"


app = FastAPI(
    title="SmartMonitor Backend API",
    lifespan=lifespan,
    description="Backend API for SmartMonitor V1",
    version="0.1.0",
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
    ],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=API_ALLOWED_HOSTS,
    www_redirect=False,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.error(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
        exc_info=(
            type(exc),
            exc,
            exc.__traceback__,
        ),
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "Internal server error"
            ),
        },
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
    mqtt_status = mqtt_manager.status()

    public_mqtt_status = {
        "connected": bool(
            mqtt_status["connected"]
        ),
    }

    if not mqtt_status["connected"]:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "mqtt": public_mqtt_status,
            },
        )

    return {
        "status": "ok",
        "mqtt": public_mqtt_status,
    }