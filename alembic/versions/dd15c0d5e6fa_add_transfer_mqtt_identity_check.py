"""store trusted retained MQTT identity checks for transfer finalization

Revision ID: dd15c0d5e6fa
Revises: cc15c0d5e6f9

Storage only. The legacy cutover table remains for compatibility, but does
not authorize an ordinary retained-identity transfer. No broker worker or
public transfer route is enabled by this migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "dd15c0d5e6fa"
down_revision: Union[str, Sequence[str], None] = "cc15c0d5e6f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_transfer_mqtt_identity_checks",
        sa.Column("transfer_id", sa.Integer(), primary_key=True),
        sa.Column("device_uid", sa.String(64), nullable=False),
        sa.Column("mqtt_device_id", sa.String(64), nullable=False),
        sa.Column("broker_reference", sa.String(128), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transfer_id"], ["device_transfers.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "broker_reference", name="uq_transfer_mqtt_identity_broker_reference"
        ),
    )


def downgrade() -> None:
    op.drop_table("device_transfer_mqtt_identity_checks")
