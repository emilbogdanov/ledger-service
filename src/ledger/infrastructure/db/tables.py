from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    MetaData,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ledger.application.idempotency import IDEMPOTENCY_KEY_MAX_LENGTH
from ledger.domain.accounts import ACCOUNT_NAME_MAX_LENGTH
from ledger.domain.journal import MEMO_MAX_LENGTH
from ledger.domain.money import Currency

# Deterministic constraint names, so migrations can refer to them and
# autogenerate never sees a renamed constraint as a new one.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# VARCHAR plus a CHECK constraint instead of a native Postgres enum: supporting another
# currency is then a one-line migration rather than an ALTER TYPE.
_currency = Enum(
    Currency,
    name="currency",
    native_enum=False,
    create_constraint=True,
    length=3,
    values_callable=lambda members: [member.value for member in members],
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012


class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    client_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(ACCOUNT_NAME_MAX_LENGTH))
    currency: Mapped[Currency] = mapped_column(_currency)
    # Null means the account has no floor and may run negative without limit.
    overdraft_limit: Mapped[int | None] = mapped_column(BigInteger)
    balance: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (CheckConstraint("overdraft_limit >= 0", name="overdraft_limit_not_negative"),)


class JournalEntryRow(Base):
    __tablename__ = "journal_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    client_id: Mapped[UUID]
    memo: Mapped[str] = mapped_column(String(MEMO_MAX_LENGTH))
    posted_at: Mapped[datetime]


class PostingRow(Base):
    __tablename__ = "postings"

    entry_id: Mapped[UUID] = mapped_column(ForeignKey("journal_entries.id"), primary_key=True)
    # Keeps the postings in the order the client sent them.
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    # Always the account's currency; stored so an entry reads back without joining accounts.
    currency: Mapped[Currency] = mapped_column(_currency)

    __table_args__ = (
        UniqueConstraint("entry_id", "account_id"),
        CheckConstraint("amount <> 0", name="amount_not_zero"),
    )


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    client_id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH), primary_key=True)
    # SHA-256 hex digest of the request the key was first used for.
    fingerprint: Mapped[str] = mapped_column(String(64))
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("journal_entries.id"))
