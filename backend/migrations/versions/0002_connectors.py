"""Connectors, and link documents to their external source

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("stats", sa.JSON()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_connectors_tenant_id", "connectors", ["tenant_id"])

    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("connector_id", sa.Uuid()))
        batch.add_column(sa.Column("external_id", sa.String(256)))
        batch.add_column(sa.Column("external_version", sa.String(128)))
        batch.add_column(sa.Column("external_parents", sa.JSON()))
        batch.create_foreign_key(
            "fk_documents_connector_id", "connectors", ["connector_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_unique_constraint("uq_documents_connector_external", ["connector_id", "external_id"])


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("uq_documents_connector_external", type_="unique")
        batch.drop_constraint("fk_documents_connector_id", type_="foreignkey")
        for col in ("external_parents", "external_version", "external_id", "connector_id"):
            batch.drop_column(col)
    op.drop_table("connectors")
