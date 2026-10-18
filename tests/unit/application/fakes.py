from collections.abc import Collection
from copy import deepcopy
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from ledger.domain.accounts import Account
from ledger.domain.ids import AccountId, ClientId


@dataclass
class LedgerState:
    accounts: dict[AccountId, Account] = field(default_factory=dict)


# Repositories hand out copies, like a database would: a service that changes an account and
# forgets to save it must fail the tests instead of passing by accident.
class InMemoryAccountRepository:
    def __init__(self, state: LedgerState) -> None:
        self._state = state

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
        return {account.id: account for account in found if account is not None}

    async def update(self, account: Account) -> None:
        assert account.id in self._state.accounts, f"account {account.id} was never added"
        self._state.accounts[account.id] = deepcopy(account)


# Each block works on a private copy of the committed state, so leaving it without commit()
# discards everything it did, the way a rolled-back transaction would.
class FakeUnitOfWork:
    def __init__(self) -> None:
        self.committed = LedgerState()
        self.commits = 0
        self._working: LedgerState | None = None

    @property
    def accounts(self) -> InMemoryAccountRepository:
        return InMemoryAccountRepository(self._state())

    async def __aenter__(self) -> Self:
        assert self._working is None, "unit of work entered twice"
        self._working = deepcopy(self.committed)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._working = None

    async def commit(self) -> None:
        self.committed = deepcopy(self._state())
        self.commits += 1

    def _state(self) -> LedgerState:
        assert self._working is not None, "repository used outside a unit of work"
        return self._working
