"""add device memberships

Revision ID: 11db167d2851
Revises: 91486562bece
Create Date: 2026-08-12 16:10:49.198544

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "11db167d2851"
down_revision: Union[str, Sequence[str], None] = "91486562bece"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # 1. Nouvelle table de liaison utilisateurs <-> appareils.
    op.create_table(
        "device_memberships",
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'member')",
            name="ck_device_memberships_role",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "device_id",
        ),
    )

    op.create_index(
        op.f("ix_device_memberships_device_id"),
        "device_memberships",
        ["device_id"],
        unique=False,
    )

    op.create_index(
        "uq_device_memberships_one_owner_per_device",
        "device_memberships",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text(
            "role = 'owner'"
        ),
    )

    # 2. Transférer les propriétaires existants AVANT
    #    de supprimer devices.owner_id.
    op.execute(
        """
        INSERT INTO device_memberships (
            user_id,
            device_id,
            role
        )
        SELECT
            owner_id,
            id,
            'owner'
        FROM devices
        WHERE owner_id IS NOT NULL
        """
    )

    # 3. L'ancien owner_id n'est plus nécessaire.
    op.drop_index(
        op.f("ix_devices_owner_id"),
        table_name="devices",
    )

    op.drop_constraint(
        op.f("devices_owner_id_fkey"),
        "devices",
        type_="foreignkey",
    )

    op.drop_column(
        "devices",
        "owner_id",
    )


def downgrade() -> None:
    """Downgrade schema."""

    # 1. Recréer l'ancien champ owner_id.
    op.add_column(
        "devices",
        sa.Column(
            "owner_id",
            sa.INTEGER(),
            autoincrement=False,
            nullable=True,
        ),
    )

    op.create_foreign_key(
        op.f("devices_owner_id_fkey"),
        "devices",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        op.f("ix_devices_owner_id"),
        "devices",
        ["owner_id"],
        unique=False,
    )

    # 2. Restaurer chaque propriétaire dans devices.owner_id.
    op.execute(
        """
        UPDATE devices
        SET owner_id = device_memberships.user_id
        FROM device_memberships
        WHERE
            device_memberships.device_id = devices.id
            AND device_memberships.role = 'owner'
        """
    )

    # 3. Supprimer le nouveau modèle multi-utilisateurs.
    op.drop_index(
        "uq_device_memberships_one_owner_per_device",
        table_name="device_memberships",
        postgresql_where=sa.text(
            "role = 'owner'"
        ),
    )

    op.drop_index(
        op.f("ix_device_memberships_device_id"),
        table_name="device_memberships",
    )

    op.drop_table(
        "device_memberships"
    )