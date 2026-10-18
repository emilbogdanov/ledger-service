from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.errors import InsufficientFundsError
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money

# Two currencies are enough to exercise per-currency balancing, and keeping the set small
# means entries regularly find several accounts to move money between.
CURRENCIES = (Currency.EUR, Currency.JPY)

CLIENT = ClientId(UUID(int=1))

overdraft_policies = st.none() | st.integers(0, 10_000)
leg_amounts = st.integers(-20_000, -1) | st.integers(1, 20_000)


class LedgerMachine(RuleBasedStateMachine):
    account_ids = Bundle("account_ids")

    def __init__(self) -> None:
        super().__init__()
        self.accounts: dict[AccountId, Account] = {}

    @initialize()
    def open_funding_accounts(self) -> None:
        # Every currency gets an account that may run negative, as the source of deposits.
        for currency in CURRENCIES:
            self._open(currency, OverdraftPolicy.unlimited())

    @rule(target=account_ids, currency=st.sampled_from(CURRENCIES), limit=overdraft_policies)
    def open_account(self, currency: Currency, limit: int | None) -> AccountId:
        return self._open(currency, OverdraftPolicy(limit)).id

    @rule(account_id=account_ids, limit=overdraft_policies)
    def change_overdraft(self, account_id: AccountId, limit: int | None) -> None:
        self.accounts[account_id].change_overdraft(OverdraftPolicy(limit))

    @precondition(lambda self: bool(self._currencies_with_two_accounts()))
    @rule(data=st.data())
    def post_entry(self, data: st.DataObject) -> None:
        eligible = self._currencies_with_two_accounts()
        currencies = data.draw(st.lists(st.sampled_from(eligible), min_size=1, unique=True))
        postings = [
            posting for currency in currencies for posting in self._draw_legs(data, currency)
        ]
        entry = JournalEntry(
            id=EntryId(uuid4()),
            client_id=CLIENT,
            postings=tuple(postings),
            memo="",
            posted_at=datetime(2026, 10, 18, tzinfo=UTC),
        )
        before = self._balances()

        try:
            entry.apply_to(self.accounts)
        except InsufficientFundsError:
            event("entry refused")
            assert self._balances() == before
            return
        event("entry applied")

        for posting in entry.postings:
            account = self.accounts[posting.account_id]
            assert account.balance == before[account.id] + posting.amount
            if posting.amount.amount < 0:
                assert account.overdraft.allows(account.balance)

    @invariant()
    def money_is_never_created_or_destroyed(self) -> None:
        totals: defaultdict[Currency, int] = defaultdict(int)
        for account in self.accounts.values():
            totals[account.currency] += account.balance.amount
        assert all(total == 0 for total in totals.values())

    def _open(self, currency: Currency, overdraft: OverdraftPolicy) -> Account:
        number = len(self.accounts) + 1
        # Sequential ids keep a failing run replayable; random ones would change on every replay.
        account = Account.open(
            account_id=AccountId(UUID(int=number)),
            client_id=CLIENT,
            name=AccountName(f"{currency} account {number}"),
            currency=currency,
            overdraft=overdraft,
        )
        self.accounts[account.id] = account
        return account

    def _draw_legs(self, data: st.DataObject, currency: Currency) -> list[Posting]:
        candidates = [
            account.id for account in self.accounts.values() if account.currency is currency
        ]
        chosen = data.draw(st.lists(st.sampled_from(candidates), min_size=2, unique=True))
        amounts = data.draw(
            st.lists(leg_amounts, min_size=len(chosen) - 1, max_size=len(chosen) - 1)
        )
        # The last account takes up the difference; when the others already net to zero there
        # are at least two of them, so it simply sits this entry out.
        closing = -sum(amounts)
        legs = [*amounts, closing] if closing else amounts
        return [
            Posting(account_id, Money(amount, currency))
            for account_id, amount in zip(chosen, legs, strict=False)
        ]

    def _currencies_with_two_accounts(self) -> list[Currency]:
        counts: defaultdict[Currency, int] = defaultdict(int)
        for account in self.accounts.values():
            counts[account.currency] += 1
        return [currency for currency in CURRENCIES if counts[currency] >= 2]

    def _balances(self) -> dict[AccountId, Money]:
        return {account_id: account.balance for account_id, account in self.accounts.items()}


TestLedgerConservesMoney = LedgerMachine.TestCase
