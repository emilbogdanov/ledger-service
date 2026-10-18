from collections.abc import Collection
from types import TracebackType
from typing import Protocol, Self

from ledger.domain.accounts import Account
from ledger.domain.ids import AccountId, ClientId


class AccountRepository(Protocol):
    async def add(self, account: Account) -> None: ...

    # Other clients' accounts are left out, exactly as if they didn't exist.
    async def get(self, client_id: ClientId, account_id: AccountId) -> Account | None: ...

    # Locks the rows until the unit of work ends. Locks are taken in ascending id order so two
    # entries touching the same accounts queue up instead of deadlocking. Unknown ids and other
    # clients' accounts are left out rather than raising.
    async def lock(
        self, client_id: ClientId, account_ids: Collection[AccountId]
    ) -> dict[AccountId, Account]: ...

    async def update(self, account: Account) -> None: ...


class UnitOfWork(Protocol):
    @property
    def accounts(self) -> AccountRepository: ...

    async def __aenter__(self) -> Self: ...

    # Leaving the block without commit() rolls back.
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
