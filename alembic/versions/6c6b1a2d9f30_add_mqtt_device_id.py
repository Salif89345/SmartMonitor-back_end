"""add mqtt device id

Revision ID: 6c6b1a2d9f30
Revises: 11db167d2851
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6c6b1a2d9f30"
down_revision: Union[str, Sequence[str], None] = "11db167d2851"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "mqtt_device_id",
            sa.String(length=64),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_devices_mqtt_device_id",
        "devices",
        ["mqtt_device_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_devices_mqtt_device_id",
        table_name="devices",
    )

    op.drop_column(
        "devices",
        "mqtt_device_id",
    )