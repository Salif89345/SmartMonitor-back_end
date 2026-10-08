"""bind SM-015 reset challenges to the requesting account

Revision ID: ee15c0d5e6fb
Revises: dd15c0d5e6fa

Additive preparation only. Do not apply in the service environment until the
whole transfer flow has been validated on an isolated bench.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ee15c0d5e6fb"
down_revision: Union[str, Sequence[str], None] = "dd15c0d5e6fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "device_reset_challenges",
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_reset_challenge_requester",
        "device_reset_challenges",
        "users",
        ["requested_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_device_reset_challenges_requested_by_user_id",
        "device_reset_challenges",
        ["requested_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_reset_challenges_requested_by_user_id",
        table_name="device_reset_challenges",
    )
    op.drop_constraint(
        "fk_reset_challenge_requester",
        "device_reset_challenges",
        type_="foreignkey",
    )
    op.drop_column("device_reset_challenges", "requested_by_user_id")
