from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, ClientId
from ledger.domain.money import Money
from ledger.infrastructure.db.tables import AccountRow


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


def _to_account(row: AccountRow) -> Account:
    return Account(
        account_id=AccountId(row.id),
        client_id=ClientId(row.client_id),
        name=AccountName(row.name),
        currency=row.currency,
        overdraft=OverdraftPolicy(row.overdraft_limit),
        balance=Money(row.balance, row.currency),
    )
