from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.errors import (
    AccountNotFoundError,
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidEntryError,
    UnbalancedEntryError,
)
from ledger.domain.ids import AccountId, EntryId
from ledger.domain.journal import MEMO_MAX_LENGTH, JournalEntry, Posting
from ledger.domain.money import Currency, Money
from tests.unit.domain.strategies import balanced_postings

EUR = Currency.EUR
USD = Currency.USD
POSTED_AT = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)


def new_account_id() -> AccountId:
    return AccountId(uuid4())


def entry(*postings: Posting, memo: str = "Invoice INV-2026-0418") -> JournalEntry:
    return JournalEntry(id=EntryId(uuid4()), postings=postings, memo=memo, posted_at=POSTED_AT)


def account(currency: Currency = EUR, *, balance: int = 0, overdraft: int | None = 0) -> Account:
    return Account(
        account_id=new_account_id(),
        name=AccountName(f"{currency} wallet"),
        currency=currency,
        overdraft=OverdraftPolicy(overdraft),
        balance=Money(balance, currency),
    )


def by_id(*accounts: Account) -> dict[AccountId, Account]:
    return {account.id: account for account in accounts}


def test_two_offsetting_postings_make_an_entry() -> None:
    payer, payee = new_account_id(), new_account_id()

    recorded = entry(Posting(payer, Money(-4_250, EUR)), Posting(payee, Money(4_250, EUR)))

    assert [posting.amount.amount for posting in recorded.postings] == [-4_250, 4_250]


def test_entry_may_split_one_side_across_several_accounts() -> None:
    entry(
        Posting(new_account_id(), Money(-10_000, EUR)),
        Posting(new_account_id(), Money(9_700, EUR)),
        Posting(new_account_id(), Money(300, EUR)),
    )


def test_each_currency_balances_on_its_own() -> None:
    entry(
        Posting(new_account_id(), Money(-10_000, EUR)),
        Posting(new_account_id(), Money(10_000, EUR)),
        Posting(new_account_id(), Money(-10_850, USD)),
        Posting(new_account_id(), Money(10_850, USD)),
    )


def test_unbalanced_postings_are_refused() -> None:
    with pytest.raises(UnbalancedEntryError, match=r"off by 0\.05 EUR") as excinfo:
        entry(
            Posting(new_account_id(), Money(-995, EUR)),
            Posting(new_account_id(), Money(1_000, EUR)),
        )

    assert excinfo.value.imbalances == (Money(5, EUR),)


def test_amounts_in_different_currencies_never_offset_each_other() -> None:
    with pytest.raises(UnbalancedEntryError) as excinfo:
        entry(
            Posting(new_account_id(), Money(-10_000, EUR)),
            Posting(new_account_id(), Money(10_000, USD)),
        )

    assert set(excinfo.value.imbalances) == {Money(-10_000, EUR), Money(10_000, USD)}


@pytest.mark.parametrize("count", [0, 1])
def test_entry_needs_at_least_two_postings(count: int) -> None:
    postings = [Posting(new_account_id(), Money(100, EUR))][:count]

    with pytest.raises(InvalidEntryError, match="at least two postings"):
        entry(*postings)


def test_entry_posts_to_each_account_once() -> None:
    wallet = new_account_id()

    with pytest.raises(InvalidEntryError, match="only once"):
        entry(
            Posting(wallet, Money(-500, EUR)),
            Posting(wallet, Money(500, EUR)),
        )


def test_posting_must_move_money() -> None:
    with pytest.raises(InvalidEntryError, match="moves no money"):
        Posting(new_account_id(), Money(0, EUR))


def test_memo_length_is_limited() -> None:
    payer, payee = (
        Posting(new_account_id(), Money(-1, EUR)),
        Posting(new_account_id(), Money(1, EUR)),
    )

    entry(payer, payee, memo="x" * MEMO_MAX_LENGTH)
    with pytest.raises(InvalidEntryError, match="memo must be at most 500"):
        entry(payer, payee, memo="x" * (MEMO_MAX_LENGTH + 1))


def test_applying_an_entry_moves_money_between_accounts() -> None:
    payer, payee = account(balance=10_000), account(balance=500)

    entry(Posting(payer.id, Money(-2_500, EUR)), Posting(payee.id, Money(2_500, EUR))).apply_to(
        by_id(payer, payee)
    )

    assert payer.balance == Money(7_500, EUR)
    assert payee.balance == Money(3_000, EUR)


def test_refused_posting_leaves_every_account_untouched() -> None:
    # The payee comes first, so a naive one-pass apply would already have credited it.
    payee, payer = account(balance=0), account(balance=1_000)
    transfer = entry(Posting(payee.id, Money(5_000, EUR)), Posting(payer.id, Money(-5_000, EUR)))

    with pytest.raises(InsufficientFundsError):
        transfer.apply_to(by_id(payee, payer))

    assert payee.balance == Money(0, EUR)
    assert payer.balance == Money(1_000, EUR)


def test_posting_in_the_wrong_currency_leaves_every_account_untouched() -> None:
    funding, wallet = account(overdraft=None), account(USD)
    deposit = entry(Posting(funding.id, Money(-1_000, EUR)), Posting(wallet.id, Money(1_000, EUR)))

    with pytest.raises(CurrencyMismatchError):
        deposit.apply_to(by_id(funding, wallet))

    assert funding.balance == Money(0, EUR)


def test_entry_naming_an_unknown_account_is_refused() -> None:
    funding = account(overdraft=None)
    missing = new_account_id()
    deposit = entry(Posting(funding.id, Money(-1_000, EUR)), Posting(missing, Money(1_000, EUR)))

    with pytest.raises(AccountNotFoundError) as excinfo:
        deposit.apply_to(by_id(funding))

    assert excinfo.value.account_id == missing
    assert funding.balance == Money(0, EUR)


@given(balanced_postings())
def test_any_balanced_set_of_postings_is_accepted(postings: list[Posting]) -> None:
    recorded = entry(*postings)

    assert recorded.postings == tuple(postings)


@given(balanced_postings(), st.data())
def test_changing_any_one_amount_unbalances_the_entry(
    postings: list[Posting], data: st.DataObject
) -> None:
    index = data.draw(st.integers(0, len(postings) - 1))
    changed = postings[index]
    # Never zero (nothing changes) and never the exact negation (the posting would move nothing).
    delta = data.draw(
        st.integers(-1_000, 1_000).filter(lambda d: d not in {0, -changed.amount.amount})
    )
    postings[index] = Posting(
        changed.account_id, Money(changed.amount.amount + delta, changed.amount.currency)
    )

    with pytest.raises(UnbalancedEntryError) as excinfo:
        entry(*postings)

    assert excinfo.value.imbalances == (Money(delta, changed.amount.currency),)
