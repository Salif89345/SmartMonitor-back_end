"""add alarm occurrences

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alarm_occurrences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("alarm_key", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column("source_boot_count", sa.Integer(), nullable=False),
        sa.Column(
            "activation_transition_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("clear_transition_count", sa.Integer(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "cleared_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "acknowledged_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'cleared')",
            name="ck_alarm_occurrences_status",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_alarm_occurrences_severity",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id",
            "source_boot_count",
            "alarm_key",
            "activation_transition_count",
            name="uq_alarm_occurrence_source",
        ),
    )
    op.create_index(
        "ix_alarm_occurrences_device_status_activated",
        "alarm_occurrences",
        ["device_id", "status", "activated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alarm_occurrences_device_status_activated",
        table_name="alarm_occurrences",
    )
    op.drop_table("alarm_occurrences")
