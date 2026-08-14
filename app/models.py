from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    false,
    text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device_memberships: Mapped[
        list["DeviceMembership"]
    ] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    device_uid: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    mqtt_device_id: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=True,
    )

    name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )


    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )


    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device_memberships: Mapped[
        list["DeviceMembership"]
    ] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    device_channels: Mapped[
        list["DeviceChannel"]
    ] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )

class DeviceMembership(Base):
    __tablename__ = "device_memberships"

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    device_id: Mapped[int] = mapped_column(
        ForeignKey(
            "devices.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    user: Mapped["User"] = relationship(
        back_populates="device_memberships",
    )

    device: Mapped["Device"] = relationship(
        back_populates="device_memberships",
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'member')",
            name="ck_device_memberships_role",
        ),
        Index(
            "uq_device_memberships_one_owner_per_device",
            "device_id",
            unique=True,
            postgresql_where=text(
                "role = 'owner'"
            ),
        ),
    )

class DeviceChannel(Base):
    __tablename__ = "device_channels"

    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "channel_key",
            name=(
                "uq_device_channels_"
                "device_id_channel_key"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    device_id: Mapped[int] = mapped_column(
        ForeignKey(
            "devices.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )

    channel_key: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda:
            datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship(
        back_populates="device_channels"
    )