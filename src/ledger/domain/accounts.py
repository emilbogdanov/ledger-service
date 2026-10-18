from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ledger.domain.errors import CurrencyMismatchError, InsufficientFundsError, InvalidValueError
from ledger.domain.money import MAX_AMOUNT, Money

if TYPE_CHECKING:
    from ledger.domain.ids import AccountId, ClientId
    from ledger.domain.money import Currency

ACCOUNT_NAME_MAX_LENGTH: Final = 120


@dataclass(frozen=True, slots=True)
class AccountName:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise InvalidValueError("account name must not be blank")
        if len(self.value) > ACCOUNT_NAME_MAX_LENGTH:
            raise InvalidValueError(
                f"account name must be at most {ACCOUNT_NAME_MAX_LENGTH} characters"
            )
        if "\n" in self.value or "\r" in self.value:
            raise InvalidValueError("account name must be a single line")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class OverdraftPolicy:
    # How far below zero the balance may go, in minor units of the account's currency.
    # None means no floor at all: funding and settlement accounts hold the other side of every
    # deposit and withdrawal, so running negative is their normal state.
    limit: int | None

    def __post_init__(self) -> None:
        if self.limit is not None and not 0 <= self.limit <= MAX_AMOUNT:
            raise InvalidValueError(f"overdraft limit {self.limit} is outside the supported range")

    @classmethod
    def none(cls) -> OverdraftPolicy:
        return cls(0)

    @classmethod
    def up_to(cls, limit: int) -> OverdraftPolicy:
        return cls(limit)

    @classmethod
    def unlimited(cls) -> OverdraftPolicy:
        return cls(None)

    def allows(self, balance: Money) -> bool:
        return self.limit is None or balance.amount >= -self.limit


class Account:
    # The constructor rebuilds an account as stored; new accounts go through open() so they
    # always start empty.
    def __init__(
        self,
        *,
        account_id: AccountId,
        client_id: ClientId,
        name: AccountName,
        currency: Currency,
        overdraft: OverdraftPolicy,
        balance: Money,
    ) -> None:
        if balance.currency is not currency:
            raise CurrencyMismatchError(f"a {currency} account cannot hold a {balance} balance")
        self._id = account_id
        self._client_id = client_id
        self._name = name
        self._currency = currency
        self._overdraft = overdraft
        self._balance = balance

    @classmethod
    def open(
        cls,
        *,
        account_id: AccountId,
        client_id: ClientId,
        name: AccountName,
        currency: Currency,
        overdraft: OverdraftPolicy,
    ) -> Account:
        return cls(
            account_id=account_id,
            client_id=client_id,
            name=name,
            currency=currency,
            overdraft=overdraft,
            balance=Money.zero(currency),
        )

    @property
    def id(self) -> AccountId:
        return self._id

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    @property
    def name(self) -> AccountName:
        return self._name

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def overdraft(self) -> OverdraftPolicy:
        return self._overdraft

    @property
    def balance(self) -> Money:
        return self._balance

    def change_overdraft(self, overdraft: OverdraftPolicy) -> None:
        # A tighter policy applies to future spending only; money already spent stays spent.
        self._overdraft = overdraft

    def ensure_can_post(self, amount: Money) -> None:
        self._balance_after(amount)

    def post(self, amount: Money) -> None:
        self._balance = self._balance_after(amount)

    def _balance_after(self, amount: Money) -> Money:
        if amount.currency is not self._currency:
            raise CurrencyMismatchError(
                f"account {self._id} holds {self._currency}, not {amount.currency}"
            )
        balance = self._balance + amount
        # Only outgoing money is checked: an account left below its floor by a tightened
        # limit can still be paid back into, it just can't sink any further.
        if amount.amount < 0 and not self._overdraft.allows(balance):
            raise InsufficientFundsError(self._id, balance=self._balance, requested=-amount)
        return balance
