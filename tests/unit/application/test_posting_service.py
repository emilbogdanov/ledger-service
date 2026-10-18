from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ledger.application.accounts import AccountService
from ledger.application.errors import EntryNotFoundError, IdempotencyKeyReusedError
from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord, fingerprint
from ledger.application.postings import PostingService
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.errors import (
    AccountNotFoundError,
    InsufficientFundsError,
    InvalidEntryError,
    InvalidValueError,
)
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money
from tests.unit.application.fakes import FakeUnitOfWork, FixedClock, LedgerState

ACME = ClientId(uuid4())
GLOBEX = ClientId(uuid4())
NOW = datetime(2026, 10, 18, 14, 5, tzinfo=UTC)


def eur(amount: int) -> Money:
    return Money(amount, Currency.EUR)


def key(value: str) -> IdempotencyKey:
    return IdempotencyKey(value)


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def accounts(uow: FakeUnitOfWork) -> AccountService:
    return AccountService(uow)


@pytest.fixture
def postings(uow: FakeUnitOfWork) -> PostingService:
    return PostingService(uow, FixedClock(NOW))


async def open_account(
    accounts: AccountService,
    name: str,
    *,
    overdraft: OverdraftPolicy | None = None,
    client_id: ClientId = ACME,
) -> AccountId:
    account = await accounts.open_account(
        client_id,
        name=AccountName(name),
        currency=Currency.EUR,
        overdraft=overdraft or OverdraftPolicy.none(),
    )
    return account.id


async def balance(
    accounts: AccountService, account_id: AccountId, client_id: ClientId = ACME
) -> Money:
    return (await accounts.get_account(client_id, account_id)).balance


@pytest.fixture
async def bank(accounts: AccountService) -> AccountId:
    return await open_account(accounts, "Bank settlement", overdraft=OverdraftPolicy.unlimited())


@pytest.fixture
async def wallet(accounts: AccountService, postings: PostingService, bank: AccountId) -> AccountId:
    wallet = await open_account(accounts, "Customer wallet")
    await postings.transfer(
        ACME,
        source=bank,
        destination=wallet,
        amount=eur(10_000),
        memo="Top-up",
        idempotency_key=key("top-up-1"),
    )
    return wallet


@pytest.fixture
async def merchant(accounts: AccountService) -> AccountId:
    return await open_account(accounts, "Merchant payouts")


async def test_transfer_moves_money_and_records_the_entry(
    accounts: AccountService, postings: PostingService, wallet: AccountId, merchant: AccountId
) -> None:
    posted = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(2_499),
        memo="Order 10432",
        idempotency_key=key("order-10432"),
    )

    assert not posted.replayed
    assert posted.entry.postings == (Posting(wallet, eur(-2_499)), Posting(merchant, eur(2_499)))
    assert posted.entry.posted_at == NOW
    assert posted.entry.client_id == ACME
    assert await balance(accounts, wallet) == eur(7_501)
    assert await balance(accounts, merchant) == eur(2_499)
    assert await postings.get_entry(ACME, posted.entry.id) == posted.entry


async def test_entry_can_split_money_across_several_accounts(
    accounts: AccountService, postings: PostingService, wallet: AccountId, merchant: AccountId
) -> None:
    fees = await open_account(accounts, "Card fees")

    await postings.post_entry(
        ACME,
        postings=[
            Posting(wallet, eur(-5_000)),
            Posting(merchant, eur(4_855)),
            Posting(fees, eur(145)),
        ],
        memo="Order 10433 with card fee",
        idempotency_key=key("order-10433"),
    )

    assert await balance(accounts, wallet) == eur(5_000)
    assert await balance(accounts, merchant) == eur(4_855)
    assert await balance(accounts, fees) == eur(145)


async def test_retry_with_the_same_key_returns_the_first_entry_without_moving_money_again(
    accounts: AccountService,
    postings: PostingService,
    wallet: AccountId,
    merchant: AccountId,
    uow: FakeUnitOfWork,
) -> None:
    first = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(1_000),
        memo="Order 10434",
        idempotency_key=key("order-10434"),
    )
    commits = uow.commits
    retry = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(1_000),
        memo="Order 10434",
        idempotency_key=key("order-10434"),
    )

    assert retry.replayed
    assert retry.entry == first.entry
    assert uow.commits == commits
    assert await balance(accounts, wallet) == eur(9_000)


async def test_retry_listing_the_legs_in_another_order_is_still_a_retry(
    postings: PostingService, wallet: AccountId, merchant: AccountId
) -> None:
    legs = [Posting(wallet, eur(-300)), Posting(merchant, eur(300))]
    first = await postings.post_entry(ACME, postings=legs, memo="", idempotency_key=key("k-1"))

    retry = await postings.post_entry(
        ACME, postings=legs[::-1], memo="", idempotency_key=key("k-1")
    )

    assert retry.replayed
    assert retry.entry.id == first.entry.id


async def test_reusing_a_key_for_a_different_request_is_refused(
    accounts: AccountService, postings: PostingService, wallet: AccountId, merchant: AccountId
) -> None:
    await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(1_000),
        memo="Order 10435",
        idempotency_key=key("order-10435"),
    )

    with pytest.raises(IdempotencyKeyReusedError, match="order-10435"):
        await postings.transfer(
            ACME,
            source=wallet,
            destination=merchant,
            amount=eur(1_500),
            memo="Order 10435",
            idempotency_key=key("order-10435"),
        )

    assert await balance(accounts, wallet) == eur(9_000)


async def test_keys_belong_to_one_client(
    accounts: AccountService, postings: PostingService, wallet: AccountId, merchant: AccountId
) -> None:
    globex_bank = await open_account(
        accounts, "Bank settlement", overdraft=OverdraftPolicy.unlimited(), client_id=GLOBEX
    )
    globex_wallet = await open_account(accounts, "Customer wallet", client_id=GLOBEX)

    for client_id, source, destination in [
        (ACME, wallet, merchant),
        (GLOBEX, globex_bank, globex_wallet),
    ]:
        posted = await postings.transfer(
            client_id,
            source=source,
            destination=destination,
            amount=eur(700),
            memo="",
            idempotency_key=key("shared-key"),
        )
        assert not posted.replayed

    assert await balance(accounts, globex_wallet, GLOBEX) == eur(700)


async def test_refused_transfer_changes_nothing_and_frees_its_key(
    accounts: AccountService,
    postings: PostingService,
    bank: AccountId,
    wallet: AccountId,
    merchant: AccountId,
) -> None:
    with pytest.raises(InsufficientFundsError):
        await postings.transfer(
            ACME,
            source=wallet,
            destination=merchant,
            amount=eur(25_000),
            memo="Order 10436",
            idempotency_key=key("order-10436"),
        )
    assert await balance(accounts, wallet) == eur(10_000)
    assert await balance(accounts, merchant) == eur(0)

    await postings.transfer(
        ACME,
        source=bank,
        destination=wallet,
        amount=eur(15_000),
        memo="Top-up",
        idempotency_key=key("top-up-2"),
    )
    retry = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(25_000),
        memo="Order 10436",
        idempotency_key=key("order-10436"),
    )

    assert not retry.replayed
    assert await balance(accounts, merchant) == eur(25_000)


async def test_transfer_to_an_unknown_account_is_refused(
    accounts: AccountService, postings: PostingService, wallet: AccountId
) -> None:
    missing = AccountId(uuid4())

    with pytest.raises(AccountNotFoundError) as excinfo:
        await postings.transfer(
            ACME,
            source=wallet,
            destination=missing,
            amount=eur(100),
            memo="",
            idempotency_key=key("k-2"),
        )

    assert excinfo.value.account_id == missing
    assert await balance(accounts, wallet) == eur(10_000)


async def test_transfer_from_another_clients_account_is_refused(
    accounts: AccountService, postings: PostingService, merchant: AccountId
) -> None:
    globex_reserve = await open_account(
        accounts, "Reserve", overdraft=OverdraftPolicy.unlimited(), client_id=GLOBEX
    )

    with pytest.raises(AccountNotFoundError):
        await postings.transfer(
            ACME,
            source=globex_reserve,
            destination=merchant,
            amount=eur(100),
            memo="",
            idempotency_key=key("k-3"),
        )

    assert await balance(accounts, globex_reserve, GLOBEX) == eur(0)


@pytest.mark.parametrize("amount", [0, -100])
async def test_transfer_amount_must_be_positive(
    postings: PostingService, wallet: AccountId, merchant: AccountId, amount: int
) -> None:
    with pytest.raises(InvalidValueError, match="must be positive"):
        await postings.transfer(
            ACME,
            source=wallet,
            destination=merchant,
            amount=eur(amount),
            memo="",
            idempotency_key=key("k-4"),
        )


async def test_transfer_to_the_same_account_is_refused(
    postings: PostingService, wallet: AccountId
) -> None:
    with pytest.raises(InvalidEntryError, match="only once"):
        await postings.transfer(
            ACME,
            source=wallet,
            destination=wallet,
            amount=eur(100),
            memo="",
            idempotency_key=key("k-5"),
        )


def commit_competing_transfer(
    state: LedgerState,
    *,
    source: AccountId,
    destination: AccountId,
    amount: Money,
    memo: str,
    idempotency_key: IdempotencyKey,
) -> EntryId:
    entry = JournalEntry(
        id=EntryId(uuid4()),
        client_id=ACME,
        postings=(Posting(source, -amount), Posting(destination, amount)),
        memo=memo,
        posted_at=NOW,
    )
    entry.apply_to(state.accounts)
    state.entries[entry.id] = entry
    state.idempotency[ACME, idempotency_key] = IdempotencyRecord(
        ACME, idempotency_key, fingerprint(entry.postings, memo), entry.id
    )
    return entry.id


async def test_same_request_committed_concurrently_is_answered_as_a_retry(
    accounts: AccountService,
    postings: PostingService,
    uow: FakeUnitOfWork,
    wallet: AccountId,
    merchant: AccountId,
) -> None:
    competing: list[EntryId] = []
    uow.before_next_commit = lambda state: competing.append(
        commit_competing_transfer(
            state,
            source=wallet,
            destination=merchant,
            amount=eur(400),
            memo="Order 10437",
            idempotency_key=key("order-10437"),
        )
    )

    posted = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(400),
        memo="Order 10437",
        idempotency_key=key("order-10437"),
    )

    assert posted.replayed
    assert posted.entry.id == competing[0]
    assert await balance(accounts, merchant) == eur(400)


async def test_different_request_committed_concurrently_with_the_same_key_is_refused(
    accounts: AccountService,
    postings: PostingService,
    uow: FakeUnitOfWork,
    bank: AccountId,
    wallet: AccountId,
    merchant: AccountId,
) -> None:
    uow.before_next_commit = lambda state: commit_competing_transfer(
        state,
        source=bank,
        destination=merchant,
        amount=eur(900),
        memo="Refund 2231",
        idempotency_key=key("order-10438"),
    )

    with pytest.raises(IdempotencyKeyReusedError):
        await postings.transfer(
            ACME,
            source=wallet,
            destination=merchant,
            amount=eur(400),
            memo="Order 10438",
            idempotency_key=key("order-10438"),
        )

    assert await balance(accounts, wallet) == eur(10_000)
    assert await balance(accounts, merchant) == eur(900)


async def test_another_clients_entry_is_not_found(
    postings: PostingService, wallet: AccountId, merchant: AccountId
) -> None:
    posted = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(100),
        memo="",
        idempotency_key=key("k-6"),
    )

    with pytest.raises(EntryNotFoundError):
        await postings.get_entry(GLOBEX, posted.entry.id)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "must not be blank"),
        ("   ", "must not be blank"),
        ("k" * 256, "at most 255 characters"),
        ("order\n10439", "printable ASCII"),
        ("заказ-10439", "printable ASCII"),
    ],
)
def test_rejects_invalid_idempotency_keys(value: str, message: str) -> None:
    with pytest.raises(InvalidValueError, match=message):
        IdempotencyKey(value)
