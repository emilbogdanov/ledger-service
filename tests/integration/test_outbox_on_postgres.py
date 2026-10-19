from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.accounts import AccountService
from ledger.application.idempotency import IdempotencyKey
from ledger.application.postings import PostingService
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.errors import InsufficientFundsError
from ledger.domain.ids import AccountId
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.tables import OutboxEventRow
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from tests.integration.clients import ACME
from tests.unit.application.fakes import FixedClock

type Sessions = async_sessionmaker[AsyncSession]

CLOCK = FixedClock(datetime(2026, 10, 18, 19, 0, tzinfo=UTC))


async def open_account(sessions: Sessions, name: str, overdraft: OverdraftPolicy) -> AccountId:
    account = await AccountService(SqlUnitOfWork(sessions), CLOCK).open_account(
        ACME, name=AccountName(name), currency=Currency.EUR, overdraft=overdraft
    )
    return account.id


async def stored_events(sessions: Sessions) -> list[OutboxEventRow]:
    async with sessions() as session:
        return list(await session.scalars(select(OutboxEventRow).order_by(OutboxEventRow.id)))


async def test_changes_and_their_events_are_committed_together(session_factory: Sessions) -> None:
    bank = await open_account(session_factory, "Bank settlement", OverdraftPolicy.unlimited())
    wallet = await open_account(session_factory, "Customer wallet", OverdraftPolicy.none())
    postings = PostingService(SqlUnitOfWork(session_factory), CLOCK)

    posted = await postings.transfer(
        ACME,
        source=bank,
        destination=wallet,
        amount=Money(5_000, Currency.EUR),
        memo="Top-up",
        idempotency_key=IdempotencyKey("top-up-1"),
    )
    with pytest.raises(InsufficientFundsError):
        await postings.transfer(
            ACME,
            source=wallet,
            destination=bank,
            amount=Money(5_001, Currency.EUR),
            memo="Too much",
            idempotency_key=IdempotencyKey("refund-1"),
        )

    events = await stored_events(session_factory)
    assert [event.type for event in events] == ["account.opened", "account.opened", "entry.posted"]
    assert events[-1].payload["entry_id"] == str(posted.entry.id)
    assert events[-1].occurred_at == CLOCK.now()
    assert all(event.published_at is None for event in events)
    assert events[0].id < events[1].id < events[2].id
