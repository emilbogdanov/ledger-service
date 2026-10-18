import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.accounts import AccountService
from ledger.application.errors import IdempotencyKeyReusedError
from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord, fingerprint
from ledger.application.postings import PostingService
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.errors import InsufficientFundsError
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.repositories import SqlEntryRepository, SqlIdempotencyRecords
from ledger.infrastructure.db.tables import IdempotencyKeyRow, JournalEntryRow
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from tests.unit.application.fakes import FixedClock

type Sessions = async_sessionmaker[AsyncSession]

ACME = ClientId(uuid4())
CLOCK = FixedClock(datetime(2026, 10, 18, 16, 20, tzinfo=UTC))


def eur(amount: int) -> Money:
    return Money(amount, Currency.EUR)


def accounts_service(sessions: Sessions) -> AccountService:
    return AccountService(SqlUnitOfWork(sessions))


# One service, and so one unit of work, per concurrent caller, as each API request gets.
def posting_service(sessions: Sessions) -> PostingService:
    return PostingService(SqlUnitOfWork(sessions), CLOCK)


async def open_account(
    sessions: Sessions, name: str, overdraft: OverdraftPolicy | None = None
) -> AccountId:
    account = await accounts_service(sessions).open_account(
        ACME,
        name=AccountName(name),
        currency=Currency.EUR,
        overdraft=overdraft or OverdraftPolicy.none(),
    )
    return account.id


async def balance(sessions: Sessions, account_id: AccountId) -> Money:
    return (await accounts_service(sessions).get_account(ACME, account_id)).balance


async def count(sessions: Sessions, table: type[object]) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(table)) or 0


@pytest.fixture
async def bank(session_factory: Sessions) -> AccountId:
    return await open_account(session_factory, "Bank settlement", OverdraftPolicy.unlimited())


@pytest.fixture
async def wallet(session_factory: Sessions, bank: AccountId) -> AccountId:
    wallet = await open_account(session_factory, "Customer wallet")
    await posting_service(session_factory).transfer(
        ACME,
        source=bank,
        destination=wallet,
        amount=eur(10_000),
        memo="Top-up",
        idempotency_key=IdempotencyKey("top-up-1"),
    )
    return wallet


@pytest.fixture
async def merchant(session_factory: Sessions) -> AccountId:
    return await open_account(session_factory, "Merchant payouts")


async def test_transfer_is_stored_and_replayed(
    session_factory: Sessions, wallet: AccountId, merchant: AccountId
) -> None:
    postings = posting_service(session_factory)

    first = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(2_499),
        memo="Order 10432",
        idempotency_key=IdempotencyKey("order-10432"),
    )
    retry = await postings.transfer(
        ACME,
        source=wallet,
        destination=merchant,
        amount=eur(2_499),
        memo="Order 10432",
        idempotency_key=IdempotencyKey("order-10432"),
    )

    assert retry.replayed
    assert retry.entry == first.entry
    assert await postings.get_entry(ACME, first.entry.id) == first.entry
    assert await balance(session_factory, wallet) == eur(7_501)
    assert await balance(session_factory, merchant) == eur(2_499)


async def test_refused_transfer_leaves_no_trace(
    session_factory: Sessions, wallet: AccountId, merchant: AccountId
) -> None:
    entries_before = await count(session_factory, JournalEntryRow)
    keys_before = await count(session_factory, IdempotencyKeyRow)

    with pytest.raises(InsufficientFundsError):
        await posting_service(session_factory).transfer(
            ACME,
            source=wallet,
            destination=merchant,
            amount=eur(10_001),
            memo="Order 10433",
            idempotency_key=IdempotencyKey("order-10433"),
        )

    assert await count(session_factory, JournalEntryRow) == entries_before
    assert await count(session_factory, IdempotencyKeyRow) == keys_before
    assert await balance(session_factory, wallet) == eur(10_000)


async def test_overdraft_change_is_stored(session_factory: Sessions, merchant: AccountId) -> None:
    await accounts_service(session_factory).change_overdraft(
        ACME, merchant, OverdraftPolicy.up_to(5_000)
    )

    account = await accounts_service(session_factory).get_account(ACME, merchant)
    assert account.overdraft == OverdraftPolicy.up_to(5_000)


async def test_simultaneous_retries_post_once(
    session_factory: Sessions, wallet: AccountId, merchant: AccountId
) -> None:
    results = await asyncio.gather(
        *(
            posting_service(session_factory).transfer(
                ACME,
                source=wallet,
                destination=merchant,
                amount=eur(1_000),
                memo="Order 10434",
                idempotency_key=IdempotencyKey("order-10434"),
            )
            for _ in range(5)
        )
    )

    assert len({result.entry.id for result in results}) == 1
    assert sum(not result.replayed for result in results) == 1
    assert await balance(session_factory, merchant) == eur(1_000)


async def hold_key_in_open_transaction(
    session: AsyncSession, key: IdempotencyKey, *, postings: tuple[Posting, ...], memo: str
) -> EntryId:
    # Another request that has stored its entry and key but not committed yet.
    entry = JournalEntry(
        id=EntryId(uuid4()), client_id=ACME, postings=postings, memo=memo, posted_at=CLOCK.now()
    )
    await SqlEntryRepository(session).add(entry)
    await SqlIdempotencyRecords(session).add(
        IdempotencyRecord(ACME, key, fingerprint(postings, memo), entry.id)
    )
    return entry.id


async def test_key_held_by_an_uncommitted_request_is_reused_once_it_commits(
    session_factory: Sessions, bank: AccountId, wallet: AccountId, merchant: AccountId
) -> None:
    fees = await open_account(session_factory, "Card fees")
    key = IdempotencyKey("order-10435")

    async with session_factory() as other_request:
        await hold_key_in_open_transaction(
            other_request,
            key,
            postings=(Posting(bank, eur(-150)), Posting(fees, eur(150))),
            memo="Card fee",
        )
        transfer = asyncio.create_task(
            posting_service(session_factory).transfer(
                ACME,
                source=wallet,
                destination=merchant,
                amount=eur(400),
                memo="Order 10435",
                idempotency_key=key,
            )
        )
        await asyncio.sleep(0.3)
        assert not transfer.done(), "the transfer should wait on the key the other request holds"

        await other_request.commit()

        with pytest.raises(IdempotencyKeyReusedError):
            await transfer

    assert await balance(session_factory, wallet) == eur(10_000)
    assert await balance(session_factory, merchant) == eur(0)


async def test_retry_of_an_uncommitted_request_waits_for_it_and_is_replayed(
    session_factory: Sessions, wallet: AccountId, merchant: AccountId
) -> None:
    # The other request's postings reference the same accounts, and their foreign keys hold
    # share locks on those rows, so the retry already waits when it locks the accounts.
    key = IdempotencyKey("order-10436")
    legs = (Posting(wallet, eur(-400)), Posting(merchant, eur(400)))

    async with session_factory() as other_request:
        held = await hold_key_in_open_transaction(other_request, key, postings=legs, memo="")
        transfer = asyncio.create_task(
            posting_service(session_factory).transfer(
                ACME,
                source=wallet,
                destination=merchant,
                amount=eur(400),
                memo="",
                idempotency_key=key,
            )
        )
        await asyncio.sleep(0.3)
        assert not transfer.done(), "the retry should wait for the request it repeats"

        await other_request.commit()
        posted = await transfer

    assert posted.replayed
    assert posted.entry.id == held
