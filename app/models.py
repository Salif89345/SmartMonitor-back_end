from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
    text,
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

    email_verification: Mapped[
        "EmailVerification"
    ] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    auth_sessions: Mapped[
        list["AuthSession"]
    ] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    __table_args__ = (
        CheckConstraint(
            "failed_attempts >= 0",
            name=(
                "ck_email_verifications_"
                "failed_attempts_nonnegative"
            ),
        ),
        CheckConstraint(
            "send_count >= 0",
            name=(
                "ck_email_verifications_"
                "send_count_nonnegative"
            ),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    code_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    failed_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    failed_window_started_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    locked_until: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    send_window_started_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    send_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(
        back_populates="email_verification",
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )

    refresh_token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    revoked_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        back_populates="auth_sessions",
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

    events: Mapped[
        list["DeviceEvent"]
    ] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        passive_deletes=True,
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
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device: Mapped["Device"] = relationship(
        back_populates="device_channels",
    )

    measurements: Mapped[
        list["PowerMeasurement"]
    ] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan",
    )

    daily_summaries: Mapped[
        list["PowerDailySummary"]
    ] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan",
    )


class PowerMeasurement(Base):
    __tablename__ = "power_measurements"

    __table_args__ = (
        Index(
            "ix_power_measurements_channel_measured_at",
            "channel_id",
            "measured_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    channel_id: Mapped[int] = mapped_column(
        ForeignKey(
            "device_channels.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    voltage_v: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    current_a: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    power_w: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    energy_kwh: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    frequency_hz: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    power_factor: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    channel: Mapped["DeviceChannel"] = relationship(
        back_populates="measurements",
    )


class PowerDailySummary(Base):
    __tablename__ = "power_daily_summaries"

    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "summary_date",
            name=(
                "uq_power_daily_summaries_"
                "channel_date"
            ),
        ),
        Index(
            "ix_power_daily_summaries_summary_date",
            "summary_date",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    channel_id: Mapped[int] = mapped_column(
        ForeignKey(
            "device_channels.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    summary_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    sample_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    first_measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    energy_start_kwh: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    energy_end_kwh: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    consumption_kwh: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    min_power_w: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    avg_power_w: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    max_power_w: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    min_voltage_v: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    avg_voltage_v: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    max_voltage_v: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    min_current_a: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    avg_current_a: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    max_current_a: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    min_frequency_hz: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    avg_frequency_hz: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    max_frequency_hz: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    min_power_factor: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    avg_power_factor: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    max_power_factor: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    channel: Mapped["DeviceChannel"] = relationship(
        back_populates="daily_summaries",
    )


class DeviceEvent(Base):
    __tablename__ = "device_events"

    __table_args__ = (
        Index(
            "ix_device_events_device_created_at",
            "device_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    device_id: Mapped[int] = mapped_column(
        ForeignKey(
            "devices.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    data: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    device: Mapped["Device"] = relationship(
        back_populates="events",
    )
