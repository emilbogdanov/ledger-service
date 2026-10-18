from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ledger.application.history import HistoryCursor
from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.repositories import SqlAccountRepository, SqlEntryRepository
from tests.integration.clients import ACME

type Sessions = async_sessionmaker[AsyncSession]

START = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)


async def ledger_with_history(sessions: Sessions) -> tuple[AccountId, list[EntryId]]:
    bank, wallet = (
        Account.open(
            account_id=AccountId(uuid4()),
            client_id=ACME,
            name=AccountName(name),
            currency=Currency.EUR,
            overdraft=OverdraftPolicy.unlimited(),
        )
        for name in ("Bank settlement", "Customer wallet")
    )
    # Two entries share each timestamp, so the entry id has to settle their order.
    entries = [
        JournalEntry(
            id=EntryId(uuid4()),
            client_id=ACME,
            postings=(
                Posting(bank.id, Money(-(number + 1), Currency.EUR)),
                Posting(wallet.id, Money(number + 1, Currency.EUR)),
            ),
            memo=f"Payment {number + 1}",
            posted_at=START + timedelta(minutes=number // 2),
        )
        for number in range(6)
    ]
    async with sessions() as session:
        accounts = SqlAccountRepository(session)
        await accounts.add(bank)
        await accounts.add(wallet)
        for entry in entries:
            await SqlEntryRepository(session).add(entry)
        await session.commit()
    newest_first = sorted(entries, key=lambda e: (e.posted_at, e.id), reverse=True)
    return wallet.id, [entry.id for entry in newest_first]


async def test_history_follows_time_then_entry_id_across_pages(session_factory: Sessions) -> None:
    wallet, expected = await ledger_with_history(session_factory)

    seen: list[EntryId] = []
    cursor: HistoryCursor | None = None
    async with session_factory() as session:
        entries = SqlEntryRepository(session)
        while page := await entries.history(wallet, after=cursor, limit=4):
            seen += [posting.entry_id for posting in page]
            cursor = HistoryCursor(page[-1].posted_at, page[-1].entry_id)

    assert seen == expected


async def test_history_carries_amount_memo_and_time(session_factory: Sessions) -> None:
    wallet, _ = await ledger_with_history(session_factory)

    async with session_factory() as session:
        page = await SqlEntryRepository(session).history(wallet, after=None, limit=2)

    # The two newest entries share the latest timestamp.
    assert {(posting.amount.amount, posting.memo) for posting in page} == {
        (5, "Payment 5"),
        (6, "Payment 6"),
    }
    assert {posting.posted_at for posting in page} == {START + timedelta(minutes=2)}


async def test_history_page_is_read_from_the_index_without_sorting(
    engine: AsyncEngine, session_factory: Sessions
) -> None:
    wallet, expected = await ledger_with_history(session_factory)
    cursor = HistoryCursor(START + timedelta(minutes=1), expected[2])
    executed: list[tuple[str, Any]] = []

    def capture(*args: Any) -> None:
        _connection, _cursor, statement, parameters, *_ = args
        executed.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with session_factory() as session:
            # A handful of rows is cheapest to read sequentially; this asks what the planner
            # does once the table is big enough for that to stop being true.
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            await SqlEntryRepository(session).history(wallet, after=cursor, limit=50)
            statement, parameters = executed[-1]
            connection = await session.connection()
            plan = "\n".join(
                row[0]
                for row in await connection.exec_driver_sql(f"EXPLAIN {statement}", parameters)
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)

    assert "ix_postings_account_id_posted_at_entry_id" in plan
    assert "Sort" not in plan
