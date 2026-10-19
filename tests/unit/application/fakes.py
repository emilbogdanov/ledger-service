from collections.abc import Callable, Collection
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import TracebackType
from typing import Self

from ledger.application.clients import Client
from ledger.application.errors import IdempotencyKeyConflictError
from ledger.application.events import Event, PendingEvent
from ledger.application.history import AccountPosting, HistoryCursor
from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord
from ledger.domain.accounts import Account
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry

type KeySlot = tuple[ClientId, IdempotencyKey]


@dataclass
class LedgerState:
    clients: dict[str, Client] = field(default_factory=dict)
    accounts: dict[AccountId, Account] = field(default_factory=dict)
    entries: dict[EntryId, JournalEntry] = field(default_factory=dict)
    idempotency: dict[KeySlot, IdempotencyRecord] = field(default_factory=dict)
    outbox: list[Event] = field(default_factory=list)
    # When each event went out, by id; an event's id is its position in the outbox from 1.
    published: dict[int, datetime] = field(default_factory=dict)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class InMemoryClientRepository:
    def __init__(self, state: LedgerState) -> None:
        self._state = state

    async def add(self, client: Client, *, api_key_hash: str) -> None:
        assert api_key_hash not in self._state.clients, "API key hash stored twice"
        self._state.clients[api_key_hash] = client

    async def find_by_api_key_hash(self, api_key_hash: str) -> Client | None:
        return self._state.clients.get(api_key_hash)


# Repositories hand out copies, like a database would: a service that changes an account and
# forgets to save it must fail the tests instead of passing by accident.
class InMemoryAccountRepository:
    def __init__(self, state: LedgerState, locked: set[AccountId]) -> None:
        self._state = state
        self._locked = locked

    async def add(self, account: Account) -> None:
        assert account.id not in self._state.accounts, f"account {account.id} added twice"
        self._state.accounts[account.id] = deepcopy(account)

    async def get(self, client_id: ClientId, account_id: AccountId) -> Account | None:
        account = self._state.accounts.get(account_id)
        if account is None or account.client_id != client_id:
            return None
        return deepcopy(account)

    async def lock(
        self, client_id: ClientId, account_ids: Collection[AccountId]
    ) -> dict[AccountId, Account]:
        found = [await self.get(client_id, account_id) for account_id in account_ids]
        accounts = {account.id: account for account in found if account is not None}
        self._locked.update(accounts)
        return accounts

    async def update(self, account: Account) -> None:
        assert account.id in self._locked, f"account {account.id} updated without a lock"
        self._state.accounts[account.id] = deepcopy(account)


class InMemoryEntryRepository:
    def __init__(self, state: LedgerState) -> None:
        self._state = state

    async def add(self, entry: JournalEntry) -> None:
        assert entry.id not in self._state.entries, f"entry {entry.id} added twice"
        self._state.entries[entry.id] = entry

    async def get(self, client_id: ClientId, entry_id: EntryId) -> JournalEntry | None:
        entry = self._state.entries.get(entry_id)
        return entry if entry is not None and entry.client_id == client_id else None

    async def history(
        self, account_id: AccountId, *, after: HistoryCursor | None, limit: int
    ) -> list[AccountPosting]:
        postings = sorted(
            (
                AccountPosting(entry.id, posting.amount, entry.memo, entry.posted_at)
                for entry in self._state.entries.values()
                for posting in entry.postings
                if posting.account_id == account_id
            ),
            key=lambda posting: (posting.posted_at, posting.entry_id),
            reverse=True,
        )
        if after is not None:
            postings = [
                p for p in postings if (p.posted_at, p.entry_id) < (after.posted_at, after.entry_id)
            ]
        return postings[:limit]


class InMemoryIdempotencyRecords:
    def __init__(self, state: LedgerState) -> None:
        self._state = state

    async def find(self, client_id: ClientId, key: IdempotencyKey) -> IdempotencyRecord | None:
        return self._state.idempotency.get((client_id, key))

    async def add(self, record: IdempotencyRecord) -> None:
        slot = (record.client_id, record.key)
        if slot in self._state.idempotency:
            raise IdempotencyKeyConflictError(record.key)
        self._state.idempotency[slot] = record


class InMemoryOutbox:
    def __init__(self, state: LedgerState) -> None:
        self._state = state

    async def add(self, event: Event) -> None:
        self._state.outbox.append(event)

    async def claim(self, limit: int) -> list[PendingEvent]:
        pending = [
            PendingEvent(number, event)
            for number, event in enumerate(self._state.outbox, start=1)
            if number not in self._state.published
        ]
        return pending[:limit]

    async def mark_published(self, event_ids: Collection[int], *, at: datetime) -> None:
        self._state.published.update(dict.fromkeys(event_ids, at))


# Each block works on a private copy of the committed state, so leaving it without commit()
# discards everything it did, the way a rolled-back transaction would.
class FakeUnitOfWork:
    def __init__(self) -> None:
        self.committed = LedgerState()
        self.commits = 0
        # Runs once, right before the next commit, against the committed state: stands in for
        # a concurrent transaction that commits while this block is still running.
        self.before_next_commit: Callable[[LedgerState], object] | None = None
        self._working: LedgerState | None = None
        self._keys_at_start: set[KeySlot] = set()
        self._locked: set[AccountId] = set()

    @property
    def clients(self) -> InMemoryClientRepository:
        return InMemoryClientRepository(self._state())

    @property
    def accounts(self) -> InMemoryAccountRepository:
        return InMemoryAccountRepository(self._state(), self._locked)

    @property
    def entries(self) -> InMemoryEntryRepository:
        return InMemoryEntryRepository(self._state())

    @property
    def idempotency(self) -> InMemoryIdempotencyRecords:
        return InMemoryIdempotencyRecords(self._state())

    @property
    def outbox(self) -> InMemoryOutbox:
        return InMemoryOutbox(self._state())

    async def __aenter__(self) -> Self:
        assert self._working is None, "unit of work entered twice"
        self._working = deepcopy(self.committed)
        self._keys_at_start = set(self.committed.idempotency)
        self._locked = set()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._working = None

    async def commit(self) -> None:
        working = self._state()
        if self.before_next_commit is not None:
            self.before_next_commit(self.committed)
            self.before_next_commit = None
        # What a unique index on (client, key) does when two transactions insert the same key.
        for slot in working.idempotency.keys() - self._keys_at_start:
            if slot in self.committed.idempotency:
                raise IdempotencyKeyConflictError(slot[1])
        self.committed = deepcopy(working)
        self.commits += 1

    def _state(self) -> LedgerState:
        assert self._working is not None, "repository used outside a unit of work"
        return self._working
