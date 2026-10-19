from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from ledger.domain.accounts import Account
    from ledger.domain.journal import JournalEntry


# What other systems hear about. The payload is the published contract, so it holds plain
# JSON values only and changes to it must stay backwards compatible.
@dataclass(frozen=True, slots=True)
class Event:
    type: str
    occurred_at: datetime
    payload: Mapping[str, object]


def account_opened(account: Account, *, at: datetime) -> Event:
    return Event(
        type="account.opened",
        occurred_at=at,
        payload={
            "account_id": str(account.id),
            "client_id": str(account.client_id),
            "name": str(account.name),
            "currency": str(account.currency),
            "overdraft_limit": account.overdraft.limit,
        },
    )


def entry_posted(entry: JournalEntry) -> Event:
    return Event(
        type="entry.posted",
        occurred_at=entry.posted_at,
        payload={
            "entry_id": str(entry.id),
            "client_id": str(entry.client_id),
            "memo": entry.memo,
            "postings": [
                {
                    "account_id": str(posting.account_id),
                    "amount": posting.amount.amount,
                    "currency": str(posting.amount.currency),
                }
                for posting in entry.postings
            ],
        },
    )
