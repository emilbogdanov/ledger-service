import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.errors import IdempotencyKeyConflictError
from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord
from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.repositories import (
    SqlAccountRepository,
    SqlEntryRepository,
    SqlIdempotencyRecords,
)

type Sessions = async_sessionmaker[AsyncSession]

ACME = ClientId(uuid4())
KEY = IdempotencyKey("payout-2026-10-18-0001")


@pytest.fixture
async def entry_ids(session_factory: Sessions) -> list[EntryId]:
    # Every record points at a real entry, so two of them are stored up front.
    payer, payee = (
        Account.open(
            account_id=AccountId(uuid4()),
            client_id=ACME,
            name=AccountName(name),
            currency=Currency.EUR,
            overdraft=OverdraftPolicy.unlimited(),
        )
        for name in ("Settlement", "Payouts")
    )
    entries = [
        JournalEntry(
            id=EntryId(uuid4()),
            client_id=ACME,
            postings=(
                Posting(payer.id, Money(-100, Currency.EUR)),
                Posting(payee.id, Money(100, Currency.EUR)),
            ),
            memo="",
            posted_at=datetime(2026, 10, 18, tzinfo=UTC),
        )
        for _ in range(2)
    ]
    async with session_factory() as session:
        accounts = SqlAccountRepository(session)
        await accounts.add(payer)
        await accounts.add(payee)
        for entry in entries:
            await SqlEntryRepository(session).add(entry)
        await session.commit()
    return [entry.id for entry in entries]


def record(entry_id: EntryId, *, client_id: ClientId = ACME) -> IdempotencyRecord:
    return IdempotencyRecord(client_id, KEY, "a" * 64, entry_id)


async def test_stored_record_is_found_by_client_and_key(
    session_factory: Sessions, entry_ids: list[EntryId]
) -> None:
    async with session_factory() as session:
        await SqlIdempotencyRecords(session).add(record(entry_ids[0]))
        await session.commit()

    async with session_factory() as session:
        records = SqlIdempotencyRecords(session)
        assert await records.find(ACME, KEY) == record(entry_ids[0])
        assert await records.find(ClientId(uuid4()), KEY) is None
        assert await records.find(ACME, IdempotencyKey("payout-2026-10-18-0002")) is None


async def test_taken_key_raises_and_leaves_the_transaction_usable(
    session_factory: Sessions, entry_ids: list[EntryId]
) -> None:
    async with session_factory() as session:
        await SqlIdempotencyRecords(session).add(record(entry_ids[0]))
        await session.commit()

    async with session_factory() as session:
        records = SqlIdempotencyRecords(session)
        with pytest.raises(IdempotencyKeyConflictError):
            await records.add(record(entry_ids[1]))

        assert await records.find(ACME, KEY) == record(entry_ids[0])


async def test_concurrent_insert_of_the_same_key_waits_and_then_conflicts(
    session_factory: Sessions, entry_ids: list[EntryId]
) -> None:
    async with session_factory() as first, session_factory() as second:
        await SqlIdempotencyRecords(first).add(record(entry_ids[0]))
        racing = asyncio.create_task(SqlIdempotencyRecords(second).add(record(entry_ids[1])))
        await asyncio.sleep(0.2)
        assert not racing.done(), "the second insert should wait for the first transaction"

        await first.commit()

        with pytest.raises(IdempotencyKeyConflictError):
            await racing
