from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ledger.application.accounts import AccountService
from ledger.application.clients import ClientService
from ledger.application.ports import Clock, UnitOfWork
from ledger.application.postings import PostingService


async def _nothing_to_close() -> None:
    return None


# Everything a request needs, built once at start-up. Services are cheap and get a fresh
# unit of work per request, so no transaction is ever shared between two requests.
@dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    close: Callable[[], Awaitable[None]] = _nothing_to_close

    def clients(self) -> ClientService:
        return ClientService(self.uow_factory())

    def accounts(self) -> AccountService:
        return AccountService(self.uow_factory())

    def postings(self) -> PostingService:
        return PostingService(self.uow_factory(), self.clock)
