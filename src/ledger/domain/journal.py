from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ledger.domain.errors import AccountNotFoundError, InvalidEntryError, UnbalancedEntryError
from ledger.domain.money import Money

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

    from ledger.domain.accounts import Account
    from ledger.domain.ids import AccountId, EntryId
    from ledger.domain.money import Currency

MIN_POSTINGS: Final = 2
MEMO_MAX_LENGTH: Final = 500


# A positive amount pays into the account, a negative one takes money out of it.
@dataclass(frozen=True, slots=True)
class Posting:
    account_id: AccountId
    amount: Money

    def __post_init__(self) -> None:
        if self.amount.is_zero:
            raise InvalidEntryError(f"posting to account {self.account_id} moves no money")


@dataclass(frozen=True, slots=True, kw_only=True)
class JournalEntry:
    id: EntryId
    postings: tuple[Posting, ...]
    memo: str
    posted_at: datetime

    def __post_init__(self) -> None:
        if len(self.postings) < MIN_POSTINGS:
            raise InvalidEntryError("an entry needs at least two postings")
        # One posting per account means each posting can be checked against the balance as
        # stored, and every account row is locked exactly once.
        account_ids = {posting.account_id for posting in self.postings}
        if len(account_ids) != len(self.postings):
            raise InvalidEntryError("an entry can post to each account only once")
        if len(self.memo) > MEMO_MAX_LENGTH:
            raise InvalidEntryError(f"memo must be at most {MEMO_MAX_LENGTH} characters")
        imbalances = _imbalances(self.postings)
        if imbalances:
            raise UnbalancedEntryError(imbalances)

    def apply_to(self, accounts: Mapping[AccountId, Account]) -> None:
        targets = [
            (_find(accounts, posting.account_id), posting.amount) for posting in self.postings
        ]
        # Every posting is checked before any balance moves, so a refused entry leaves all of
        # its accounts exactly as they were.
        for account, amount in targets:
            account.ensure_can_post(amount)
        for account, amount in targets:
            account.post(amount)


# Balancing is per currency: an FX entry moves euros between euro accounts and dollars between
# dollar accounts, and each side has to net to zero on its own.
def _imbalances(postings: Iterable[Posting]) -> list[Money]:
    totals: defaultdict[Currency, int] = defaultdict(int)
    for posting in postings:
        totals[posting.amount.currency] += posting.amount.amount
    return [Money(total, currency) for currency, total in totals.items() if total]


def _find(accounts: Mapping[AccountId, Account], account_id: AccountId) -> Account:
    try:
        return accounts[account_id]
    except KeyError:
        raise AccountNotFoundError(account_id) from None
