"""record the owner and ownership epoch of each power measurement

Revision ID: bc15c0d5e6f8
Revises: ab15c0d5e6f7

Not applied automatically. Existing rows are attributed to the current owner
only if no transfer has ever completed; a prior transfer makes this unsafe.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bc15c0d5e6f8"
down_revision: Union[str, Sequence[str], None] = "ab15c0d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM device_transfers WHERE state = 'completed')
            THEN RAISE EXCEPTION 'Cannot backfill measurements after a transfer';
            END IF;
            IF EXISTS (
                SELECT device_id FROM device_memberships
                WHERE role = 'owner'
                GROUP BY device_id HAVING COUNT(*) <> 1
            )
            THEN RAISE EXCEPTION 'Ambiguous owner for measurement backfill';
            END IF;
        END $$;
        """
    )
    op.create_table(
        "power_measurement_attributions",
        sa.Column("measurement_id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("epoch_transfer_id", sa.Integer(), nullable=True),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["measurement_id"], ["power_measurements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["epoch_transfer_id"], ["device_transfers.id"]),
    )
    op.create_index(
        "ix_power_measurement_attributions_device_id",
        "power_measurement_attributions", ["device_id"],
    )
    op.create_index(
        "ix_power_measurement_attributions_owner_user_id",
        "power_measurement_attributions", ["owner_user_id"],
    )
    op.create_index(
        "ix_measurement_attributions_owner_epoch",
        "power_measurement_attributions", ["owner_user_id", "epoch_transfer_id"],
    )
    op.execute(
        """
        INSERT INTO power_measurement_attributions
            (measurement_id, device_id, owner_user_id, epoch_transfer_id, attributed_at)
        SELECT m.id, c.device_id, member.user_id, NULL, m.received_at
        FROM power_measurements AS m
        JOIN device_channels AS c ON c.id = m.channel_id
        JOIN device_memberships AS member
          ON member.device_id = c.device_id AND member.role = 'owner'
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_measurement_attributions_owner_epoch",
        table_name="power_measurement_attributions",
    )
    op.drop_index(
        "ix_power_measurement_attributions_owner_user_id",
        table_name="power_measurement_attributions",
    )
    op.drop_index(
        "ix_power_measurement_attributions_device_id",
        table_name="power_measurement_attributions",
    )
    op.drop_table("power_measurement_attributions")
