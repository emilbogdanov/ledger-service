from collections.abc import Collection
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from ledger.application.clients import Client
from ledger.application.events import Event
from ledger.application.history import AccountPosting, HistoryCursor
from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord
from ledger.domain.accounts import Account
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry


class Clock(Protocol):
    def now(self) -> datetime: ...


class ClientRepository(Protocol):
    async def add(self, client: Client, *, api_key_hash: str) -> None: ...

    async def find_by_api_key_hash(self, api_key_hash: str) -> Client | None: ...


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

    # Only for accounts locked in the current unit of work.
    async def update(self, account: Account) -> None: ...


class EntryRepository(Protocol):
    async def add(self, entry: JournalEntry) -> None: ...

    async def get(self, client_id: ClientId, entry_id: EntryId) -> JournalEntry | None: ...

    # The account's postings newest first, ties broken by entry id descending, starting
    # right after the cursor.
    async def history(
        self, account_id: AccountId, *, after: HistoryCursor | None, limit: int
    ) -> list[AccountPosting]: ...


class IdempotencyRecords(Protocol):
    async def find(self, client_id: ClientId, key: IdempotencyKey) -> IdempotencyRecord | None: ...

    # Raises IdempotencyKeyConflictError if another request already took the key. A store
    # may only find out at commit, so UnitOfWork.commit() can raise it as well.
    async def add(self, record: IdempotencyRecord) -> None: ...


class Outbox(Protocol):
    # Written in the caller's transaction, so an event exists exactly when the change it
    # describes was committed.
    async def add(self, event: Event) -> None: ...


class UnitOfWork(Protocol):
    @property
    def clients(self) -> ClientRepository: ...

    @property
    def accounts(self) -> AccountRepository: ...

    @property
    def entries(self) -> EntryRepository: ...

    @property
    def idempotency(self) -> IdempotencyRecords: ...

    @property
    def outbox(self) -> Outbox: ...

    async def __aenter__(self) -> Self: ...

    # Leaving the block without commit() rolls back.
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
