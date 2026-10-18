from hypothesis import strategies as st

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
