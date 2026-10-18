"""create accounts, journal entries, postings and idempotency keys

Revision ID: fbe63377ed52
Revises:
Create Date: 2026-10-18 19:17:48.134446+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fbe63377ed52"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The currencies as they stood when this revision was written; adding one later is its own
# migration that replaces these constraints.
_CURRENCIES = ", ".join(
    f"'{code}'"
    for code in (
        "BGN",
        "CHF",
        "CZK",
        "DKK",
        "EUR",
        "GBP",
        "HUF",
        "JPY",
        "KWD",
        "NOK",
        "PLN",
        "RON",
        "SEK",
        "USD",
    )
)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("overdraft_limit", sa.BigInteger(), nullable=True),
        sa.Column("balance", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.CheckConstraint(f"currency IN ({_CURRENCIES})", name="ck_accounts_currency"),
        sa.CheckConstraint("overdraft_limit >= 0", name="ck_accounts_overdraft_limit_not_negative"),
    )
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("memo", sa.String(length=500), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_journal_entries"),
    )
    op.create_table(
        "postings",
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.PrimaryKeyConstraint("entry_id", "position", name="pk_postings"),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["journal_entries.id"], name="fk_postings_entry_id_journal_entries"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_postings_account_id_accounts"
        ),
        sa.UniqueConstraint("entry_id", "account_id", name="uq_postings_entry_id_account_id"),
        sa.CheckConstraint(f"currency IN ({_CURRENCIES})", name="ck_postings_currency"),
        sa.CheckConstraint("amount <> 0", name="ck_postings_amount_not_zero"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("client_id", "key", name="pk_idempotency_keys"),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["journal_entries.id"],
            name="fk_idempotency_keys_entry_id_journal_entries",
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("postings")
    op.drop_table("journal_entries")
    op.drop_table("accounts")
