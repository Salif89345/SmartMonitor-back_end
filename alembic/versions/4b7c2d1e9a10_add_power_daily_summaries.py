"""add power daily summaries

Revision ID: 4b7c2d1e9a10
Revises: 8d3f2a1c4b70
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4b7c2d1e9a10"
down_revision: Union[str, Sequence[str], None] = (
    "8d3f2a1c4b70"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "power_daily_summaries",

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
            "summary_date",
            sa.Date(),
            nullable=False,
        ),

        sa.Column(
            "sample_count",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "first_measured_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),

        sa.Column(
            "last_measured_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),

        sa.Column(
            "energy_start_kwh",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "energy_end_kwh",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "consumption_kwh",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "min_power_w",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "avg_power_w",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "max_power_w",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "min_voltage_v",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "avg_voltage_v",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "max_voltage_v",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "min_current_a",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "avg_current_a",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "max_current_a",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "min_frequency_hz",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "avg_frequency_hz",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "max_frequency_hz",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "min_power_factor",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "avg_power_factor",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "max_power_factor",
            sa.Float(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["device_channels.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),

        sa.UniqueConstraint(
            "channel_id",
            "summary_date",
            name=(
                "uq_power_daily_summaries_"
                "channel_date"
            ),
        ),
    )

    op.create_index(
        "ix_power_daily_summaries_summary_date",
        "power_daily_summaries",
        ["summary_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_power_daily_summaries_summary_date",
        table_name="power_daily_summaries",
    )

    op.drop_table(
        "power_daily_summaries"
    )