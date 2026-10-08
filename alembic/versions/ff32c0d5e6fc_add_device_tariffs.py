"""Keep tariff preferences on the backend, per user and device.

Revision ID: ff32c0d5e6fc
Revises: f2a3b4c5d6e7

This SM-032 branch is independent from the unfinished SM-015 transfer
migrations. Apply this revision explicitly, not ``upgrade heads``.
"""

from alembic import op
import sqlalchemy as sa


revision = "ff32c0d5e6fc"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_tariffs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("eur_per_kwh", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "device_id"),
        sa.CheckConstraint(
            "eur_per_kwh >= 0 AND eur_per_kwh <= 10",
            name="ck_device_tariffs_range",
        ),
    )
    op.create_table(
        "device_alarm_configurations",
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )


def downgrade() -> None:
    op.drop_table("device_alarm_configurations")
    op.drop_table("device_tariffs")
