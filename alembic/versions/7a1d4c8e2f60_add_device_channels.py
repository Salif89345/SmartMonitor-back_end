"""add device channels

Revision ID: 7a1d4c8e2f60
Revises: 6c6b1a2d9f30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a1d4c8e2f60"
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "6c6b1a2d9f30"

branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


def upgrade() -> None:
    op.create_table(
        "device_channels",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "channel_key",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id",
            "channel_key",
            name=(
                "uq_device_channels_"
                "device_id_channel_key"
            ),
        ),
    )

    op.create_index(
        "ix_device_channels_device_id",
        "device_channels",
        ["device_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_channels_device_id",
        table_name="device_channels",
    )

    op.drop_table("device_channels")