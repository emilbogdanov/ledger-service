from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.reconciliation import (
    AuditFindings,
    BalanceMismatch,
    EntryImbalance,
    MisdirectedPosting,
    PostingProblem,
)
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.infrastructure.db.tables import AccountRow, JournalEntryRow, PostingRow


class SqlLedgerAudit:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def examine(self, *, limit: int) -> AuditFindings:
        async with self._session_factory() as session:
            # Every query below then reads the same snapshot, and read-only guarantees the
            # audit can never be the thing that changes the ledger.
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            return AuditFindings(
                accounts_checked=await _count(session, AccountRow),
                entries_checked=await _count(session, JournalEntryRow),
                balance_mismatches=await _balance_mismatches(session, limit),
                entry_imbalances=await _entry_imbalances(session, limit),
                misdirected_postings=await _misdirected_postings(session, limit),
            )


async def _count(session: AsyncSession, table: type[AccountRow | JournalEntryRow]) -> int:
    return await session.scalar(select(func.count()).select_from(table)) or 0


async def _balance_mismatches(session: AsyncSession, limit: int) -> list[BalanceMismatch]:
    totals = (
        select(PostingRow.account_id, func.sum(PostingRow.amount).label("total"))
        .group_by(PostingRow.account_id)
        .subquery()
    )
    from_postings = func.coalesce(totals.c.total, 0)
    rows = await session.execute(
        select(
            AccountRow.id,
            AccountRow.client_id,
            AccountRow.currency,
            AccountRow.balance,
            from_postings,
        )
        .outerjoin(totals, totals.c.account_id == AccountRow.id)
        .where(AccountRow.balance != from_postings)
        .order_by(AccountRow.id)
        .limit(limit)
    )
    return [
        BalanceMismatch(AccountId(account_id), ClientId(client_id), currency, stored, int(total))
        for account_id, client_id, currency, stored, total in rows
    ]


async def _entry_imbalances(session: AsyncSession, limit: int) -> list[EntryImbalance]:
    total = func.sum(PostingRow.amount)
    rows = await session.execute(
        select(PostingRow.entry_id, PostingRow.currency, total)
        .group_by(PostingRow.entry_id, PostingRow.currency)
        .having(total != 0)
        .order_by(PostingRow.entry_id, PostingRow.currency)
        .limit(limit)
    )
    return [
        EntryImbalance(EntryId(entry_id), currency, int(off_by))
        for entry_id, currency, off_by in rows
    ]


async def _misdirected_postings(session: AsyncSession, limit: int) -> list[MisdirectedPosting]:
    found: list[MisdirectedPosting] = []
    checks = (
        (PostingProblem.WRONG_CURRENCY, PostingRow.currency != AccountRow.currency),
        (PostingProblem.WRONG_CLIENT, JournalEntryRow.client_id != AccountRow.client_id),
    )
    for problem, condition in checks:
        rows = await session.execute(
            select(PostingRow.entry_id, PostingRow.account_id)
            .join(AccountRow, AccountRow.id == PostingRow.account_id)
            .join(JournalEntryRow, JournalEntryRow.id == PostingRow.entry_id)
            .where(condition)
            .order_by(PostingRow.entry_id, PostingRow.account_id)
            .limit(limit - len(found))
        )
        found += [
            MisdirectedPosting(EntryId(entry_id), AccountId(account_id), problem)
            for entry_id, account_id in rows
        ]
        if len(found) == limit:
            break
    return found
