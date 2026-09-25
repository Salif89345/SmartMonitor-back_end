"""record verified MQTT credential cutover before completing ownership transfer

Revision ID: cc15c0d5e6f9
Revises: bc15c0d5e6f8

Storage only: no broker integration, worker or transfer route is enabled here.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cc15c0d5e6f9"
down_revision: Union[str, Sequence[str], None] = "bc15c0d5e6f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_transfer_mqtt_cutovers",
        sa.Column("transfer_id", sa.Integer(), primary_key=True),
        sa.Column("device_uid", sa.String(64), nullable=False),
        sa.Column("broker_reference", sa.String(128), nullable=False),
        sa.Column("old_access_revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("new_access_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transfer_id"], ["device_transfers.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "broker_reference", name="uq_transfer_mqtt_cutover_broker_reference"
        ),
    )


def downgrade() -> None:
    op.drop_table("device_transfer_mqtt_cutovers")
