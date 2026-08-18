"""add device events

Revision ID: c9d2e3f4a5b6
Revises: b8c1d2e3f4a5
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d2e3f4a5b6"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "b8c1d2e3f4a5"

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
        "device_events",

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
            "event_type",
            sa.String(length=64),
            nullable=False,
        ),

        sa.Column(
            "data",
            sa.JSON(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),
    )

    op.create_index(
        "ix_device_events_device_created_at",
        "device_events",
        [
            "device_id",
            "created_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_events_device_created_at",
        table_name="device_events",
    )

    op.drop_table(
        "device_events"
    )
