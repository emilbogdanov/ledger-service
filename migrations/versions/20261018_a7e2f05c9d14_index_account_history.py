"""copy posted_at onto postings and index each account's history

Revision ID: a7e2f05c9d14
Revises: 3d9c41a7b2e8
Create Date: 2026-10-18 20:10:37.552091+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7e2f05c9d14"
down_revision: str | None = "3d9c41a7b2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable first so existing postings can be backfilled from their entries.
    op.add_column("postings", sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE postings SET posted_at = journal_entries.posted_at"
        " FROM journal_entries WHERE journal_entries.id = postings.entry_id"
    )
    op.alter_column("postings", "posted_at", nullable=False)
    op.create_index(
        "ix_postings_account_id_posted_at_entry_id",
        "postings",
        ["account_id", "posted_at", "entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_postings_account_id_posted_at_entry_id", table_name="postings")
    op.drop_column("postings", "posted_at")
