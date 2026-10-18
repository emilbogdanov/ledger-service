"""add API clients and make every client id refer to one

Revision ID: 3d9c41a7b2e8
Revises: fbe63377ed52
Create Date: 2026-10-18 19:45:12.408113+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3d9c41a7b2e8"
down_revision: str | None = "fbe63377ed52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLIENT_REFERENCES = ("accounts", "journal_entries", "idempotency_keys")


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_clients"),
        sa.UniqueConstraint("api_key_hash", name="uq_clients_api_key_hash"),
    )
    # Fails on a database holding rows for clients that were never registered, which can only
    # be test data from before this revision.
    for table in _CLIENT_REFERENCES:
        op.create_foreign_key(
            f"fk_{table}_client_id_clients", table, "clients", ["client_id"], ["id"]
        )


def downgrade() -> None:
    for table in _CLIENT_REFERENCES:
        op.drop_constraint(f"fk_{table}_client_id_clients", table, type_="foreignkey")
    op.drop_table("clients")
