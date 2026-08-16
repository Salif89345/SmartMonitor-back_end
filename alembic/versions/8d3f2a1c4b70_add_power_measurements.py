"""add power measurements

Revision ID: 8d3f2a1c4b70
Revises: 7a1d4c8e2f60
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d3f2a1c4b70"
down_revision: Union[str, Sequence[str], None] = "7a1d4c8e2f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "power_measurements",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "measured_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "voltage_v",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "current_a",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "power_w",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "energy_kwh",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "frequency_hz",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "power_factor",
            sa.Float(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["device_channels.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_power_measurements_channel_measured_at",
        "power_measurements",
        ["channel_id", "measured_at"],
        unique=False,
       )


def downgrade() -> None:
    op.drop_index(
        "ix_power_measurements_channel_measured_at",
        table_name="power_measurements",
      )

    op.drop_table("power_measurements")