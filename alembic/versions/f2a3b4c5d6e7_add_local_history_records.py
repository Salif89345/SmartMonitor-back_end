"""add local history import ledger

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "local_history_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_local_history_records_sequence_positive",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id",
            "sequence",
            name="uq_local_history_records_device_sequence",
        ),
    )

    op.create_index(
        "ix_local_history_records_device_captured_at",
        "local_history_records",
        ["device_id", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_local_history_records_device_captured_at",
        table_name="local_history_records",
    )
    op.drop_table("local_history_records")
