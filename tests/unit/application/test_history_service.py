from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ledger.application.accounts import AccountService
from ledger.application.history import MAX_PAGE_SIZE, HistoryCursor, HistoryService
from ledger.application.idempotency import IdempotencyKey
from ledger.application.postings import PostingService
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.errors import AccountNotFoundError, InvalidValueError
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.money import Currency, Money
from tests.unit.application.fakes import FakeUnitOfWork, FixedClock

ACME = ClientId(uuid4())
GLOBEX = ClientId(uuid4())


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))


@pytest.fixture
def history(uow: FakeUnitOfWork) -> HistoryService:
    return HistoryService(uow)


async def open_account(uow: FakeUnitOfWork, name: str, client_id: ClientId = ACME) -> AccountId:
    account = await AccountService(uow).open_account(
        client_id,
        name=AccountName(name),
        currency=Currency.EUR,
        overdraft=OverdraftPolicy.unlimited(),
    )
    return account.id


@pytest.fixture
async def wallet(uow: FakeUnitOfWork, clock: FixedClock) -> AccountId:
    # Five payments into the wallet, a minute apart: 100, 200, ... 500 cents.
    bank = await open_account(uow, "Bank settlement")
    wallet = await open_account(uow, "Customer wallet")
    postings = PostingService(uow, clock)
    for number in range(1, 6):
        await postings.transfer(
            ACME,
            source=bank,
            destination=wallet,
            amount=Money(number * 100, Currency.EUR),
            memo=f"Payment {number}",
            idempotency_key=IdempotencyKey(f"payment-{number}"),
        )
        clock.advance(timedelta(minutes=1))
    return wallet


async def test_history_pages_run_newest_first(history: HistoryService, wallet: AccountId) -> None:
    amounts: list[list[int]] = []
    cursor: HistoryCursor | None = None
    while True:
        page = await history.history(ACME, wallet, after=cursor, limit=2)
        amounts.append([posting.amount.amount for posting in page.postings])
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert amounts == [[500, 400], [300, 200], [100]]


async def test_page_that_reaches_the_end_has_no_cursor(
    history: HistoryService, wallet: AccountId
) -> None:
    page = await history.history(ACME, wallet, after=None, limit=5)

    assert len(page.postings) == 5
    assert page.next_cursor is None
    assert page.postings[0].memo == "Payment 5"


async def test_postings_at_the_same_moment_are_ordered_by_entry_id(
    uow: FakeUnitOfWork, history: HistoryService
) -> None:
    bank, wallet = await open_account(uow, "Bank"), await open_account(uow, "Wallet")
    same_moment = PostingService(uow, FixedClock(datetime(2026, 10, 18, 12, 0, tzinfo=UTC)))
    for number in range(4):
        await same_moment.transfer(
            ACME,
            source=bank,
            destination=wallet,
            amount=Money(1, Currency.EUR),
            memo="",
            idempotency_key=IdempotencyKey(f"batch-{number}"),
        )

    first = await history.history(ACME, wallet, after=None, limit=2)
    assert first.next_cursor is not None
    second = await history.history(ACME, wallet, after=first.next_cursor, limit=2)

    ids = [posting.entry_id for posting in first.postings + second.postings]
    assert ids == sorted(ids, reverse=True)
    assert len(set(ids)) == 4


async def test_account_without_postings_has_an_empty_history(
    uow: FakeUnitOfWork, history: HistoryService
) -> None:
    empty = await open_account(uow, "Dormant")

    page = await history.history(ACME, empty, after=None, limit=10)

    assert page.postings == []
    assert page.next_cursor is None


async def test_cursor_past_the_oldest_posting_gives_an_empty_page(
    history: HistoryService, wallet: AccountId
) -> None:
    before_everything = HistoryCursor(datetime(2020, 1, 1, tzinfo=UTC), EntryId(uuid4()))

    page = await history.history(ACME, wallet, after=before_everything, limit=10)

    assert page.postings == []


async def test_another_clients_history_is_not_found(
    uow: FakeUnitOfWork, history: HistoryService
) -> None:
    theirs = await open_account(uow, "Globex reserve", GLOBEX)

    with pytest.raises(AccountNotFoundError):
        await history.history(ACME, theirs, after=None, limit=10)


@pytest.mark.parametrize("limit", [0, MAX_PAGE_SIZE + 1])
async def test_page_size_is_bounded(history: HistoryService, wallet: AccountId, limit: int) -> None:
    with pytest.raises(InvalidValueError, match="page size"):
        await history.history(ACME, wallet, after=None, limit=limit)
