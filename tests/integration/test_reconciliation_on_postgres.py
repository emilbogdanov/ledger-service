import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.accounts import AccountService
from ledger.application.idempotency import IdempotencyKey
from ledger.application.postings import PostedEntry, PostingService
from ledger.application.reconciliation import (
    BalanceMismatch,
    EntryImbalance,
    MisdirectedPosting,
    PostingProblem,
    ReconciliationReport,
    ReconciliationService,
)
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.errors import InsufficientFundsError
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.audit import SqlLedgerAudit
from ledger.infrastructure.db.repositories import SqlEntryRepository
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from tests.integration.clients import ACME, GLOBEX
from tests.unit.application.fakes import FixedClock

type Sessions = async_sessionmaker[AsyncSession]

CLOCK = FixedClock(datetime(2026, 10, 19, 6, 0, tzinfo=UTC))


def eur(amount: int) -> Money:
    return Money(amount, Currency.EUR)


async def reconcile(sessions: Sessions, *, limit: int = 100) -> ReconciliationReport:
    return await ReconciliationService(SqlLedgerAudit(sessions), CLOCK).reconcile(limit=limit)


async def open_account(
    sessions: Sessions, name: str, *, client_id: ClientId = ACME, unlimited: bool = False
) -> AccountId:
    account = await AccountService(SqlUnitOfWork(sessions), CLOCK).open_account(
        client_id,
        name=AccountName(name),
        currency=Currency.EUR,
        overdraft=OverdraftPolicy.unlimited() if unlimited else OverdraftPolicy.none(),
    )
    return account.id


async def transfer(
    sessions: Sessions, source: AccountId, destination: AccountId, amount: int, key: str
) -> PostedEntry:
    return await PostingService(SqlUnitOfWork(sessions), CLOCK).transfer(
        ACME,
        source=source,
        destination=destination,
        amount=eur(amount),
        memo="",
        idempotency_key=IdempotencyKey(key),
    )


async def tamper(sessions: Sessions, statement: str, **parameters: object) -> None:
    async with sessions() as session:
        await session.execute(text(statement), parameters)
        await session.commit()


@pytest.fixture
async def bank(session_factory: Sessions) -> AccountId:
    return await open_account(session_factory, "Bank settlement", unlimited=True)


@pytest.fixture
async def wallet(session_factory: Sessions, bank: AccountId) -> AccountId:
    wallet = await open_account(session_factory, "Customer wallet")
    await transfer(session_factory, bank, wallet, 10_000, "top-up-1")
    return wallet


async def test_ledger_written_through_the_services_reconciles(
    session_factory: Sessions, wallet: AccountId
) -> None:
    merchant = await open_account(session_factory, "Merchant payouts")
    await transfer(session_factory, wallet, merchant, 2_500, "order-1")
    with pytest.raises(InsufficientFundsError):
        await transfer(session_factory, wallet, merchant, 50_000, "order-2")

    report = await reconcile(session_factory)

    assert report.is_clean
    assert not report.truncated
    assert report.checked_at == CLOCK.now()
    assert (report.findings.accounts_checked, report.findings.entries_checked) == (3, 2)


async def test_tampered_balance_is_reported(session_factory: Sessions, wallet: AccountId) -> None:
    await tamper(
        session_factory, "UPDATE accounts SET balance = balance + 5 WHERE id = :id", id=wallet
    )

    report = await reconcile(session_factory)

    assert report.findings.balance_mismatches == [
        BalanceMismatch(wallet, ACME, Currency.EUR, stored=10_005, from_postings=10_000)
    ]
    assert report.discrepancies == 1


async def test_tampered_posting_unbalances_its_entry_and_its_account(
    session_factory: Sessions, wallet: AccountId
) -> None:
    merchant = await open_account(session_factory, "Merchant payouts")
    posted = await transfer(session_factory, wallet, merchant, 2_500, "order-3")
    await tamper(
        session_factory,
        "UPDATE postings SET amount = 2400 WHERE entry_id = :entry AND account_id = :account",
        entry=posted.entry.id,
        account=merchant,
    )

    report = await reconcile(session_factory)

    assert report.findings.entry_imbalances == [EntryImbalance(posted.entry.id, Currency.EUR, -100)]
    assert report.findings.balance_mismatches == [
        BalanceMismatch(merchant, ACME, Currency.EUR, stored=2_500, from_postings=2_400)
    ]


async def test_posting_in_another_currency_is_reported(
    session_factory: Sessions, wallet: AccountId
) -> None:
    await tamper(
        session_factory,
        "UPDATE postings SET currency = 'USD' WHERE account_id = :account",
        account=wallet,
    )

    report = await reconcile(session_factory)

    (misdirected,) = report.findings.misdirected_postings
    assert (misdirected.account_id, misdirected.problem) == (wallet, PostingProblem.WRONG_CURRENCY)
    assert {imbalance.currency for imbalance in report.findings.entry_imbalances} == {
        Currency.EUR,
        Currency.USD,
    }


async def test_posting_to_another_clients_account_is_reported(
    session_factory: Sessions, bank: AccountId
) -> None:
    theirs = await open_account(session_factory, "Globex reserve", client_id=GLOBEX)
    # What a bug in client scoping would write: an Acme entry moving money into Globex's books.
    leak = JournalEntry(
        id=EntryId(uuid4()),
        client_id=ACME,
        postings=(Posting(bank, eur(-700)), Posting(theirs, eur(700))),
        memo="",
        posted_at=CLOCK.now(),
    )
    async with session_factory() as session:
        await SqlEntryRepository(session).add(leak)
        await session.commit()

    report = await reconcile(session_factory)

    assert MisdirectedPosting(leak.id, theirs, PostingProblem.WRONG_CLIENT) in (
        report.findings.misdirected_postings
    )


async def test_findings_are_capped_at_the_limit(session_factory: Sessions, bank: AccountId) -> None:
    wallets = [await open_account(session_factory, f"Wallet {number}") for number in range(5)]
    for number, wallet in enumerate(wallets):
        await transfer(session_factory, bank, wallet, 100, f"seed-{number}")
    await tamper(session_factory, "UPDATE accounts SET balance = balance + 1")

    report = await reconcile(session_factory, limit=3)

    assert len(report.findings.balance_mismatches) == 3
    assert report.truncated


async def test_reconciling_while_transfers_run_finds_nothing(
    session_factory: Sessions, bank: AccountId
) -> None:
    wallets = [await open_account(session_factory, f"Wallet {number}") for number in range(4)]
    stop = asyncio.Event()

    async def keep_transferring(writer: int) -> None:
        number = 0
        while not stop.is_set():
            wallet = wallets[(writer + number) % len(wallets)]
            # Two top-ups for every withdrawal, so most withdrawals find money to take.
            source, destination = (bank, wallet) if number % 3 else (wallet, bank)
            with suppress(InsufficientFundsError):
                await transfer(session_factory, source, destination, 50, f"live-{writer}-{number}")
            number += 1

    writers = [asyncio.create_task(keep_transferring(writer)) for writer in range(4)]
    try:
        reports = [await reconcile(session_factory) for _ in range(20)]
    finally:
        stop.set()
        await asyncio.gather(*writers)

    assert all(report.is_clean for report in reports)
    assert reports[-1].findings.entries_checked > reports[0].findings.entries_checked
