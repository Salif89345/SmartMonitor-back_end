from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)


MeasurementQuality = Literal[
    "ok",
    "incoherent",
    "unavailable",
]


MeasurementFreshness = Literal[
    "unknown",
    "fresh",
    "stale",
]


class UserCreate(BaseModel):
    email: EmailStr

    password: str = Field(
        min_length=8,
        max_length=128,
    )


class UserPublic(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    email: EmailStr
    is_active: bool
    email_verified: bool


class VerifyEmailRequest(BaseModel):
    email: EmailStr

    code: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^[0-9]{6}$",
    )


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class VerificationMessageResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: EmailStr

    password: str = Field(
        min_length=1,
        max_length=128,
    )


class LoginResponse(BaseModel):
    authenticated: bool
    access_token: str
    refresh_token: str
    token_type: str
    user: UserPublic


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(
        min_length=32,
        max_length=512,
    )


class LogoutRequest(BaseModel):
    refresh_token: str = Field(
        min_length=32,
        max_length=512,
    )


class LogoutResponse(BaseModel):
    message: str


class DeviceAccessPublic(BaseModel):
    id: int
    device_uid: str
    name: str | None
    is_active: bool
    created_at: datetime

    role: Literal[
        "owner",
        "member",
    ]


class DeviceListPublic(DeviceAccessPublic):
    availability: Literal[
        "online",
        "offline",
        "unknown",
    ]

    last_state_received_at: datetime | None


class DeviceChannelPublic(BaseModel):
    id: int
    channel_key: str
    name: str | None
    is_enabled: bool


class DeviceTelemetryPublic(BaseModel):
    source: Literal[
        "mqtt",
        "persisted",
    ]

    freshness: Literal[
        "fresh",
        "stale",
    ]

    received_at: datetime
    measured_at: datetime | None
    age_seconds: float

    model: str | None = None
    hardware_revision: str | None = None
    firmware_version: str | None = None

    ntp_synchronized: bool | None = None

    sensor_status: str | None = None
    energy_status: str | None = None

    sensor_freshness: MeasurementFreshness | None = None
    sensor_age_ms: int | None = None

    energy_freshness: MeasurementFreshness | None = None
    energy_age_ms: int | None = None

    temperature_c: float | None = None
    temperature_quality: MeasurementQuality | None = None

    humidity_pct: float | None = None
    humidity_quality: MeasurementQuality | None = None

    voltage_v: float | None = None
    voltage_quality: MeasurementQuality | None = None

    current_a: float | None = None
    current_quality: MeasurementQuality | None = None

    power_w: float | None = None
    power_quality: MeasurementQuality | None = None

    energy_kwh: float | None = None
    energy_quality: MeasurementQuality | None = None

    frequency_hz: float | None = None
    frequency_quality: MeasurementQuality | None = None

    power_factor: float | None = None
    power_factor_quality: MeasurementQuality | None = None


class DeviceDetailPublic(DeviceListPublic):
    channels: list[DeviceChannelPublic]

    telemetry_channel_id: int | None
    telemetry: DeviceTelemetryPublic | None




class DeviceClaimRequest(BaseModel):
    device_uid: str = Field(
        min_length=15,
        max_length=15,
        pattern=r"^SM-[0-9A-F]{12}$",
    )

    nonce: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[0-9A-F]{32}$",
    )


class AddDeviceMemberRequest(BaseModel):
    email: EmailStr


class DeviceMemberPublic(BaseModel):
    user_id: int
    email: EmailStr
    role: Literal["member"]


class DeviceCommandResponse(BaseModel):
    device_id: int
    request_id: str
    result: Literal["ack", "nack"]
    error_code: str | None = None
    message: str
    data: dict[str, Any] | None = None


class HistoryMetricSummary(BaseModel):
    min: float | None
    avg: float | None
    max: float | None


class HistorySummary(BaseModel):
    consumption_kwh: float | None

    power_w: HistoryMetricSummary
    voltage_v: HistoryMetricSummary
    current_a: HistoryMetricSummary
    frequency_hz: HistoryMetricSummary
    power_factor: HistoryMetricSummary


class HistoryPoint(BaseModel):
    bucket_start: datetime
    bucket_end: datetime
    sample_count: int

    power_w: float | None
    voltage_v: float | None
    current_a: float | None
    frequency_hz: float | None
    power_factor: float | None

    consumption_kwh: float | None


class HistoryPeriod(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True
    )

    from_: datetime = Field(
        alias="from"
    )

    to: datetime


class DeviceHistoryResponse(BaseModel):
    device_id: int
    channel_id: int

    period: HistoryPeriod

    target_points: int
    returned_points: int
    resolution_seconds: int

    source: Literal[
        "detailed",
        "daily",
    ]

    summary: HistorySummary

    points: list[HistoryPoint]


class DeviceEventPublic(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    device_id: int
    event_type: str
    data: dict[str, Any] | None
    created_at: datetime
