from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger.domain.accounts import ACCOUNT_NAME_MAX_LENGTH, Account, AccountName, OverdraftPolicy
from ledger.domain.errors import CurrencyMismatchError, InsufficientFundsError, InvalidValueError
from ledger.domain.ids import AccountId
from ledger.domain.money import MAX_AMOUNT, Currency, Money

EUR = Currency.EUR


def eur(amount: int) -> Money:
    return Money(amount, EUR)


def make_account(
    *, balance: int = 0, overdraft: OverdraftPolicy | None = None, currency: Currency = EUR
) -> Account:
    return Account(
        account_id=AccountId(uuid4()),
        name=AccountName("Operating account"),
        currency=currency,
        overdraft=overdraft or OverdraftPolicy.none(),
        balance=Money(balance, currency),
    )


def test_opened_account_starts_empty() -> None:
    account = Account.open(
        account_id=AccountId(uuid4()),
        name=AccountName("Customer wallet"),
        currency=Currency.JPY,
        overdraft=OverdraftPolicy.none(),
    )

    assert account.balance == Money.zero(Currency.JPY)


def test_incoming_money_raises_the_balance() -> None:
    account = make_account(balance=1_000)

    account.post(eur(250))

    assert account.balance == eur(1_250)


def test_spending_the_whole_balance_is_allowed_without_overdraft() -> None:
    account = make_account(balance=1_000)

    account.post(eur(-1_000))

    assert account.balance == eur(0)


def test_spending_past_zero_without_overdraft_is_refused() -> None:
    account = make_account(balance=1_000)

    with pytest.raises(InsufficientFundsError) as excinfo:
        account.post(eur(-1_001))

    assert excinfo.value.account_id == account.id
    assert excinfo.value.balance == eur(1_000)
    assert excinfo.value.requested == eur(1_001)
    assert "cannot spend 10.01 EUR from a balance of 10.00 EUR" in str(excinfo.value)
    assert account.balance == eur(1_000)


def test_overdraft_limit_is_inclusive() -> None:
    account = make_account(balance=100, overdraft=OverdraftPolicy.up_to(5_000))

    account.post(eur(-5_100))

    assert account.balance == eur(-5_000)
    with pytest.raises(InsufficientFundsError):
        account.post(eur(-1))


def test_unlimited_overdraft_never_refuses_spending() -> None:
    account = make_account(overdraft=OverdraftPolicy.unlimited())

    account.post(eur(-MAX_AMOUNT))

    assert account.balance == eur(-MAX_AMOUNT)


def test_account_below_a_tightened_floor_can_be_paid_into_but_not_spent_from() -> None:
    account = make_account(overdraft=OverdraftPolicy.up_to(10_000))
    account.post(eur(-8_000))

    account.change_overdraft(OverdraftPolicy.up_to(5_000))
    account.post(eur(1_000))

    assert account.balance == eur(-7_000)
    with pytest.raises(InsufficientFundsError):
        account.post(eur(-1))


def test_posting_in_another_currency_is_refused() -> None:
    account = make_account(balance=1_000)

    with pytest.raises(CurrencyMismatchError, match="holds EUR, not USD"):
        account.post(Money(100, Currency.USD))

    assert account.balance == eur(1_000)


def test_balance_that_would_leave_the_supported_range_is_refused() -> None:
    account = make_account(balance=MAX_AMOUNT)

    with pytest.raises(InvalidValueError):
        account.ensure_can_post(eur(1))

    assert account.balance == eur(MAX_AMOUNT)


def test_stored_balance_must_be_in_the_account_currency() -> None:
    with pytest.raises(CurrencyMismatchError):
        Account(
            account_id=AccountId(uuid4()),
            name=AccountName("Settlement"),
            currency=EUR,
            overdraft=OverdraftPolicy.unlimited(),
            balance=Money(0, Currency.USD),
        )


@given(
    balance=st.integers(-1_000_000, 1_000_000),
    limit=st.none() | st.integers(0, 1_000_000),
    amount=st.integers(-2_000_000, 2_000_000),
)
def test_a_posting_moves_the_balance_by_exactly_its_amount_or_not_at_all(
    balance: int, limit: int | None, amount: int
) -> None:
    account = make_account(balance=balance, overdraft=OverdraftPolicy(limit))
    sinks_below_floor = amount < 0 and limit is not None and balance + amount < -limit

    if sinks_below_floor:
        with pytest.raises(InsufficientFundsError):
            account.post(eur(amount))
        assert account.balance == eur(balance)
    else:
        account.post(eur(amount))
        assert account.balance == eur(balance + amount)


@pytest.mark.parametrize("limit", [-1, MAX_AMOUNT + 1])
def test_overdraft_limit_must_be_in_range(limit: int) -> None:
    with pytest.raises(InvalidValueError, match="overdraft limit"):
        OverdraftPolicy.up_to(limit)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "must not be blank"),
        ("   ", "must not be blank"),
        ("x" * (ACCOUNT_NAME_MAX_LENGTH + 1), "at most 120 characters"),
        ("Payroll\nMarch", "single line"),
    ],
)
def test_rejects_invalid_account_names(name: str, message: str) -> None:
    with pytest.raises(InvalidValueError, match=message):
        AccountName(name)


def test_accepts_an_account_name_at_the_length_limit() -> None:
    assert len(str(AccountName("x" * ACCOUNT_NAME_MAX_LENGTH))) == ACCOUNT_NAME_MAX_LENGTH
