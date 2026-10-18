from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ledger.domain.errors import CurrencyMismatchError, InvalidValueError


class Currency(StrEnum):
    BGN = "BGN"
    CHF = "CHF"
    CZK = "CZK"
    DKK = "DKK"
    EUR = "EUR"
    GBP = "GBP"
    HUF = "HUF"
    JPY = "JPY"
    KWD = "KWD"
    NOK = "NOK"
    PLN = "PLN"
    RON = "RON"
    SEK = "SEK"
    USD = "USD"

    @property
    def decimal_places(self) -> int:
        return _DECIMAL_PLACES_EXCEPTIONS.get(self, 2)


# ISO 4217 minor units for the supported currencies that don't split into hundredths.
_DECIMAL_PLACES_EXCEPTIONS: Final = {Currency.JPY: 0, Currency.KWD: 3}

# Amounts and balances are stored as BIGINT, so anything wider is refused here instead of by the
# database halfway through a transaction. The range is symmetric so negating never overflows.
MAX_AMOUNT: Final = 2**63 - 1


# Amounts are integers in the currency's minor unit (cents, fils, yen), so no arithmetic
# here ever rounds.
@dataclass(frozen=True, slots=True)
class Money:
    amount: int
    currency: Currency

    def __post_init__(self) -> None:
        if not -MAX_AMOUNT <= self.amount <= MAX_AMOUNT:
            raise InvalidValueError(f"amount {self.amount} is outside the supported range")

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(0, currency)

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount >= other.amount

    def __str__(self) -> str:
        places = self.currency.decimal_places
        whole, fraction = divmod(abs(self.amount), 10**places)
        digits = f"{whole}.{fraction:0{places}d}" if places else str(whole)
        sign = "-" if self.amount < 0 else ""
        return f"{sign}{digits} {self.currency}"

    def _require_same_currency(self, other: Money) -> None:
        if other.currency is not self.currency:
            raise CurrencyMismatchError(f"cannot combine {self.currency} with {other.currency}")
