from hypothesis import strategies as st

from ledger.domain.ids import AccountId
from ledger.domain.journal import Posting
from ledger.domain.money import MAX_AMOUNT, Currency, Money

currencies = st.sampled_from(Currency)

# A third of the supported range, so summing up to three of these always stays in range.
_AMOUNT_BOUND = MAX_AMOUNT // 3


def money(currency: Currency) -> st.SearchStrategy[Money]:
    return st.builds(Money, st.integers(-_AMOUNT_BOUND, _AMOUNT_BOUND), st.just(currency))


@st.composite
def money_pairs(draw: st.DrawFn) -> tuple[Money, Money]:
    currency = draw(currencies)
    return draw(money(currency)), draw(money(currency))


# Small enough that the closing leg, which offsets up to five others, stays in range.
_leg_amounts = st.integers(-(10**15), 10**15).filter(bool)


@st.composite
def balanced_postings(draw: st.DrawFn) -> list[Posting]:
    amounts: list[Money] = []
    for currency in draw(st.lists(currencies, min_size=1, max_size=3, unique=True)):
        legs = draw(st.lists(_leg_amounts, min_size=1, max_size=5))
        closing = -sum(legs)
        amounts.extend(Money(leg, currency) for leg in ([*legs, closing] if closing else legs))
    account_ids = draw(
        st.lists(st.uuids(), min_size=len(amounts), max_size=len(amounts), unique=True)
    )
    postings = [
        Posting(AccountId(account_id), amount)
        for account_id, amount in zip(account_ids, amounts, strict=True)
    ]
    return draw(st.permutations(postings))
