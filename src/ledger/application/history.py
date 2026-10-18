from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ledger.domain.errors import AccountNotFoundError, InvalidValueError

if TYPE_CHECKING:
    from datetime import datetime

    # The ports name the types below, so importing them at run time here would be circular.
    from ledger.application.ports import UnitOfWork
    from ledger.domain.ids import AccountId, ClientId, EntryId
    from ledger.domain.money import Money

MAX_PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class AccountPosting:
    entry_id: EntryId
    amount: Money
    memo: str
    posted_at: datetime


# Where a page ends in an account's history, which runs newest first with ties broken by
# entry id, so every posting has exactly one place.
@dataclass(frozen=True, slots=True)
class HistoryCursor:
    posted_at: datetime
    entry_id: EntryId


@dataclass(frozen=True, slots=True)
class HistoryPage:
    postings: list[AccountPosting]
    next_cursor: HistoryCursor | None


class HistoryService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def history(
        self,
        client_id: ClientId,
        account_id: AccountId,
        *,
        after: HistoryCursor | None,
        limit: int,
    ) -> HistoryPage:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidValueError(f"page size must be 1 to {MAX_PAGE_SIZE}")
        async with self._uow as uow:
            if await uow.accounts.get(client_id, account_id) is None:
                raise AccountNotFoundError(account_id)
            # One extra row says whether another page exists without a COUNT query.
            found = await uow.entries.history(account_id, after=after, limit=limit + 1)
        page = found[:limit]
        if len(found) <= limit:
            return HistoryPage(page, next_cursor=None)
        last = page[-1]
        return HistoryPage(page, next_cursor=HistoryCursor(last.posted_at, last.entry_id))
