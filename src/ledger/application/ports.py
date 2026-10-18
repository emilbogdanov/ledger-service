from collections.abc import Collection
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord
from ledger.domain.accounts import Account
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry


class Clock(Protocol):
    def now(self) -> datetime: ...


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


class EntryRepository(Protocol):
    async def add(self, entry: JournalEntry) -> None: ...

    async def get(self, client_id: ClientId, entry_id: EntryId) -> JournalEntry | None: ...


class IdempotencyRecords(Protocol):
    async def find(self, client_id: ClientId, key: IdempotencyKey) -> IdempotencyRecord | None: ...

    async def add(self, record: IdempotencyRecord) -> None: ...


class UnitOfWork(Protocol):
    @property
    def accounts(self) -> AccountRepository: ...

    @property
    def entries(self) -> EntryRepository: ...

    @property
    def idempotency(self) -> IdempotencyRecords: ...

    async def __aenter__(self) -> Self: ...

    # Leaving the block without commit() rolls back.
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    # Raises IdempotencyKeyConflictError if another request stored one of this block's
    # idempotency keys first.
    async def commit(self) -> None: ...
