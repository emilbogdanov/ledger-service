from collections.abc import Collection

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.ext.asyncio import AsyncSession

from ledger.application.errors import IdempotencyKeyConflictError
from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord
from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Money
from ledger.infrastructure.db.tables import (
    AccountRow,
    IdempotencyKeyRow,
    JournalEntryRow,
    PostingRow,
)


class SqlAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Rows locked by this transaction. Strong references keep them in the session's
        # identity map, so update() changes the very row that was locked.
        self._locked: dict[AccountId, AccountRow] = {}

    async def add(self, account: Account) -> None:
        self._session.add(
            AccountRow(
                id=account.id,
                client_id=account.client_id,
                name=str(account.name),
                currency=account.currency,
                overdraft_limit=account.overdraft.limit,
                balance=account.balance.amount,
            )
        )

    async def get(self, client_id: ClientId, account_id: AccountId) -> Account | None:
        row = await self._session.scalar(
            select(AccountRow).where(AccountRow.id == account_id, AccountRow.client_id == client_id)
        )
        return _to_account(row) if row is not None else None

    async def lock(
        self, client_id: ClientId, account_ids: Collection[AccountId]
    ) -> dict[AccountId, Account]:
        if not account_ids:
            return {}
        rows = await self._session.scalars(
            select(AccountRow)
            .where(AccountRow.client_id == client_id, AccountRow.id.in_(account_ids))
            # Postgres takes the row locks as the sorted result comes out, so this ORDER BY
            # is what makes every transaction lock accounts in the same order.
            .order_by(AccountRow.id)
            .with_for_update()
        )
        accounts: dict[AccountId, Account] = {}
        for row in rows:
            account = _to_account(row)
            self._locked[account.id] = row
            accounts[account.id] = account
        return accounts

    async def update(self, account: Account) -> None:
        row = self._locked.get(account.id)
        # Writing back an account read without the lock could overwrite a balance that
        # another transaction moved in the meantime.
        if row is None:
            raise RuntimeError(f"account {account.id} must be locked before it is updated")
        row.name = str(account.name)
        row.overdraft_limit = account.overdraft.limit
        row.balance = account.balance.amount


class SqlEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: JournalEntry) -> None:
        await self._session.execute(
            insert(JournalEntryRow).values(
                id=entry.id, client_id=entry.client_id, memo=entry.memo, posted_at=entry.posted_at
            )
        )
        await self._session.execute(
            insert(PostingRow),
            [
                {
                    "entry_id": entry.id,
                    "position": position,
                    "account_id": posting.account_id,
                    "amount": posting.amount.amount,
                    "currency": posting.amount.currency,
                }
                for position, posting in enumerate(entry.postings)
            ],
        )

    async def get(self, client_id: ClientId, entry_id: EntryId) -> JournalEntry | None:
        entry = await self._session.scalar(
            select(JournalEntryRow).where(
                JournalEntryRow.id == entry_id, JournalEntryRow.client_id == client_id
            )
        )
        if entry is None:
            return None
        postings = await self._session.scalars(
            select(PostingRow).where(PostingRow.entry_id == entry_id).order_by(PostingRow.position)
        )
        return JournalEntry(
            id=EntryId(entry.id),
            client_id=ClientId(entry.client_id),
            postings=tuple(
                Posting(AccountId(row.account_id), Money(row.amount, row.currency))
                for row in postings
            ),
            memo=entry.memo,
            posted_at=entry.posted_at,
        )


class SqlIdempotencyRecords:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, client_id: ClientId, key: IdempotencyKey) -> IdempotencyRecord | None:
        row = await self._session.scalar(
            select(IdempotencyKeyRow).where(
                IdempotencyKeyRow.client_id == client_id, IdempotencyKeyRow.key == str(key)
            )
        )
        if row is None:
            return None
        return IdempotencyRecord(client_id, key, row.fingerprint, EntryId(row.entry_id))

    async def add(self, record: IdempotencyRecord) -> None:
        # ON CONFLICT rather than letting the INSERT fail: Postgres waits for a transaction
        # holding the same key to finish and then skips the row, which leaves this
        # transaction usable instead of aborted.
        inserted = await self._session.scalar(
            upsert(IdempotencyKeyRow)
            .values(
                client_id=record.client_id,
                key=str(record.key),
                fingerprint=record.fingerprint,
                entry_id=record.entry_id,
            )
            .on_conflict_do_nothing(index_elements=["client_id", "key"])
            .returning(IdempotencyKeyRow.entry_id)
        )
        if inserted is None:
            raise IdempotencyKeyConflictError(record.key)


def _to_account(row: AccountRow) -> Account:
    return Account(
        account_id=AccountId(row.id),
        client_id=ClientId(row.client_id),
        name=AccountName(row.name),
        currency=row.currency,
        overdraft=OverdraftPolicy(row.overdraft_limit),
        balance=Money(row.balance, row.currency),
    )
