from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)


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


class LoginRequest(BaseModel):
    email: EmailStr

    password: str = Field(
        min_length=1,
        max_length=128,
    )


class LoginResponse(BaseModel):
    authenticated: bool
    access_token: str
    token_type: str
    user: UserPublic


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



class DeviceCommandResponse(BaseModel):
    device_id: int
    request_id: str
    result: Literal["ack", "nack"]
    error_code: str | None = None
    message: str
    data: dict[str, Any] | None = None