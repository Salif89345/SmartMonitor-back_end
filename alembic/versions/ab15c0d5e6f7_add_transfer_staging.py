"""stage reset-proof challenges, ownership requests and durable notices

Revision ID: ab15c0d5e6f7
Revises: f2a3b4c5d6e7

This migration creates storage only. It does not enable a transfer endpoint.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ab15c0d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_reset_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("challenge_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("challenge_digest", name="uq_reset_challenge_digest"),
    )
    op.create_index(
        "ix_device_reset_challenges_device_id",
        "device_reset_challenges", ["device_id"],
    )

    op.create_table(
        "device_transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("previous_owner_user_id", sa.Integer(), nullable=False),
        sa.Column("next_owner_user_id", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.String(36), nullable=False),
        sa.Column("reset_generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("cause", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["previous_owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["next_owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["challenge_id"], ["device_reset_challenges.id"]
        ),
        sa.UniqueConstraint(
            "device_id", "reset_generation", name="uq_transfer_reset_generation"
        ),
        sa.UniqueConstraint("challenge_id", name="uq_transfer_challenge"),
        sa.CheckConstraint("reset_generation > 0", name="ck_transfer_generation"),
        sa.CheckConstraint(
            "state IN ('pending', 'completed', 'cancelled')",
            name="ck_transfer_state",
        ),
    )
    op.create_index("ix_device_transfers_device_id", "device_transfers", ["device_id"])
    op.create_index(
        "uq_device_transfers_one_pending", "device_transfers", ["device_id"],
        unique=True, postgresql_where=sa.text("state = 'pending'"),
    )

    op.create_table(
        "device_transfer_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transfer_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("body", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["transfer_id"], ["device_transfers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "transfer_id", "user_id", "phase", name="uq_transfer_notice_phase"
        ),
        sa.CheckConstraint(
            "phase IN ('pending', 'completed')", name="ck_transfer_notice_phase"
        ),
    )
    op.create_index(
        "ix_device_transfer_notices_user_id", "device_transfer_notices", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_device_transfer_notices_user_id", table_name="device_transfer_notices")
    op.drop_table("device_transfer_notices")
    op.drop_index("uq_device_transfers_one_pending", table_name="device_transfers")
    op.drop_index("ix_device_transfers_device_id", table_name="device_transfers")
    op.drop_table("device_transfers")
    op.drop_index("ix_device_reset_challenges_device_id", table_name="device_reset_challenges")
    op.drop_table("device_reset_challenges")
