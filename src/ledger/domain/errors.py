from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ledger.domain.ids import AccountId
    from ledger.domain.money import Money


# The API maps each DomainError subclass to an HTTP status in a single place,
# so the domain never needs to know about HTTP.
class DomainError(Exception):
    pass


class CurrencyMismatchError(DomainError):
    pass


class InvalidValueError(DomainError):
    pass


class InsufficientFundsError(DomainError):
    def __init__(self, account_id: AccountId, *, balance: Money, requested: Money) -> None:
        super().__init__(
            f"account {account_id} cannot spend {requested} from a balance of {balance}"
        )
        self.account_id = account_id
        self.balance = balance
        self.requested = requested
