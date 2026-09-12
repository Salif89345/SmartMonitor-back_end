"""add unique power measurement timestamp

Revision ID: d0e1f2a3b4c5
Revises: c9d2e3f4a5b6
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_power_measurements_channel_measured_at",
        "power_measurements",
        ["channel_id", "measured_at"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_power_measurements_channel_measured_at",
        "power_measurements",
        type_="unique",
    )
