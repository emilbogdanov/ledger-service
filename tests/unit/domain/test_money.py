import operator
from collections.abc import Callable
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger.domain.errors import CurrencyMismatchError, InvalidValueError
from ledger.domain.money import MAX_AMOUNT, Currency, Money
from tests.unit.domain.strategies import currencies, money, money_pairs

EUR = Currency.EUR


@given(money_pairs())
def test_addition_is_commutative(pair: tuple[Money, Money]) -> None:
    a, b = pair
    assert a + b == b + a


@given(currencies.flatmap(lambda currency: st.tuples(*[money(currency)] * 3)))
def test_addition_is_associative(triple: tuple[Money, Money, Money]) -> None:
    a, b, c = triple
    assert (a + b) + c == a + (b + c)


@given(currencies.flatmap(money))
def test_adding_the_negation_gives_zero(amount: Money) -> None:
    assert (amount + -amount).is_zero
    assert amount + -amount == Money.zero(amount.currency)


@given(money_pairs())
def test_subtraction_adds_the_negation(pair: tuple[Money, Money]) -> None:
    a, b = pair
    assert a - b == a + -b
    assert (a - b) + b == a


@given(money_pairs())
def test_ordering_follows_minor_unit_amounts(pair: tuple[Money, Money]) -> None:
    a, b = pair
    assert (a < b) == (a.amount < b.amount)
    assert (a <= b) == (a.amount <= b.amount)
    assert (a > b) == (a.amount > b.amount)
    assert (a >= b) == (a.amount >= b.amount)


@pytest.mark.parametrize(
    "combine",
    [operator.add, operator.sub, operator.lt, operator.le, operator.gt, operator.ge],
)
def test_amounts_in_different_currencies_never_combine(
    combine: Callable[[Money, Money], object],
) -> None:
    with pytest.raises(CurrencyMismatchError, match="EUR with USD"):
        combine(Money(100, EUR), Money(100, Currency.USD))


@pytest.mark.parametrize("amount", [MAX_AMOUNT, -MAX_AMOUNT])
def test_accepts_amounts_at_the_edge_of_the_range(amount: int) -> None:
    assert Money(amount, EUR).amount == amount


@pytest.mark.parametrize("amount", [MAX_AMOUNT + 1, -MAX_AMOUNT - 1])
def test_rejects_amounts_outside_the_range(amount: int) -> None:
    with pytest.raises(InvalidValueError, match="outside the supported range"):
        Money(amount, EUR)


def test_arithmetic_that_would_overflow_is_rejected() -> None:
    with pytest.raises(InvalidValueError):
        Money(MAX_AMOUNT, EUR) + Money(1, EUR)


def test_equality_includes_the_currency() -> None:
    assert Money(100, EUR) != Money(100, Currency.USD)


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (123456, EUR, "1234.56 EUR"),
        (-5, EUR, "-0.05 EUR"),
        (0, Currency.USD, "0.00 USD"),
        (500, Currency.JPY, "500 JPY"),
        (-1, Currency.JPY, "-1 JPY"),
        (1, Currency.KWD, "0.001 KWD"),
    ],
)
def test_formats_in_major_units(amount: int, currency: Currency, expected: str) -> None:
    assert str(Money(amount, currency)) == expected


@given(currencies.flatmap(money))
def test_formatted_amount_converts_back_to_the_same_minor_units(amount: Money) -> None:
    major, code = str(amount).split(" ")

    assert code == amount.currency
    assert Decimal(major).scaleb(amount.currency.decimal_places) == amount.amount


@pytest.mark.parametrize(
    ("currency", "places"),
    [(EUR, 2), (Currency.BGN, 2), (Currency.JPY, 0), (Currency.KWD, 3)],
)
def test_decimal_places_follow_iso_4217(currency: Currency, places: int) -> None:
    assert currency.decimal_places == places
