import asyncio
import random
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.accounts import AccountService
from ledger.application.idempotency import IdempotencyKey
from ledger.application.postings import PostedEntry, PostingService
from ledger.application.reconciliation import ReconciliationService
from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.errors import InsufficientFundsError
from ledger.domain.ids import AccountId
from ledger.domain.journal import Posting
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.audit import SqlLedgerAudit
from ledger.infrastructure.db.tables import AccountRow, JournalEntryRow, PostingRow
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from tests.integration.clients import ACME
from tests.unit.application.fakes import FixedClock

type Sessions = async_sessionmaker[AsyncSession]

CLOCK = FixedClock(datetime(2026, 10, 18, 21, 0, tzinfo=UTC))
WALLETS = 8
OPENING_BALANCE = 5_000
ENTRIES = 400
# Fixed so every run sends the same entries; the order Postgres runs them in still varies.
SEED = 20261018


def eur(amount: int) -> Money:
    return Money(amount, Currency.EUR)


def postings(sessions: Sessions) -> PostingService:
    # A unit of work holds one transaction, so every concurrent caller needs its own.
    return PostingService(SqlUnitOfWork(sessions), CLOCK)


async def open_wallets(sessions: Sessions) -> tuple[AccountId, list[Account]]:
    accounts = AccountService(SqlUnitOfWork(sessions), CLOCK)
    funding = await accounts.open_account(
        ACME,
        name=AccountName("Bank settlement"),
        currency=Currency.EUR,
        overdraft=OverdraftPolicy.unlimited(),
    )
    wallets = [
        await accounts.open_account(
            ACME,
            name=AccountName(f"Wallet {number}"),
            currency=Currency.EUR,
            # Half may dip below zero, half may not, so both kinds of floor get hit.
            overdraft=OverdraftPolicy.up_to(2_000) if number % 2 else OverdraftPolicy.none(),
        )
        for number in range(1, WALLETS + 1)
    ]
    for wallet in wallets:
        await postings(sessions).transfer(
            ACME,
            source=funding.id,
            destination=wallet.id,
            amount=eur(OPENING_BALANCE),
            memo="Opening balance",
            idempotency_key=IdempotencyKey(f"opening-{wallet.id}"),
        )
    return funding.id, wallets


def random_legs(rng: random.Random, wallet_ids: list[AccountId]) -> list[Posting]:
    # Mostly plain transfers, some three-way splits. Pairs are drawn in both directions, so
    # A->B and B->A run at the same time: the classic deadlock if locks weren't ordered.
    if rng.random() < 0.75:
        source, destination = rng.sample(wallet_ids, 2)
        amount = rng.randint(1, 3_000)
        return [Posting(source, eur(-amount)), Posting(destination, eur(amount))]
    source, first, second = rng.sample(wallet_ids, 3)
    to_first, to_second = rng.randint(1, 2_000), rng.randint(1, 2_000)
    return [
        Posting(first, eur(to_first)),
        Posting(source, eur(-(to_first + to_second))),
        Posting(second, eur(to_second)),
    ]


async def test_concurrent_entries_never_deadlock_or_create_money(
    session_factory: Sessions,
) -> None:
    funding, wallets = await open_wallets(session_factory)
    rng = random.Random(SEED)
    wallet_ids = [wallet.id for wallet in wallets]

    results = await asyncio.gather(
        *(
            postings(session_factory).post_entry(
                ACME,
                postings=random_legs(rng, wallet_ids),
                memo=f"Concurrent entry {number}",
                idempotency_key=IdempotencyKey(f"concurrent-{number}"),
            )
            for number in range(ENTRIES)
        ),
        return_exceptions=True,
    )

    unexpected = [r for r in results if not isinstance(r, PostedEntry | InsufficientFundsError)]
    assert unexpected == [], f"only refusals for lack of funds are expected: {unexpected[:3]}"
    posted = sum(isinstance(result, PostedEntry) for result in results)
    # Both outcomes must actually happen, or the test proves less than it claims.
    assert 0 < posted < ENTRIES

    async with session_factory() as session:
        stored = await session.execute(select(AccountRow.id, AccountRow.balance))
        balances = dict(stored.all())
        sums = await session.execute(
            select(PostingRow.account_id, func.sum(PostingRow.amount)).group_by(
                PostingRow.account_id
            )
        )
        posted_sums = dict(sums.all())
        entries = await session.scalar(select(func.count()).select_from(JournalEntryRow))

    assert sum(balances.values()) == 0
    assert balances == posted_sums
    assert entries == WALLETS + posted
    assert balances[funding] == -WALLETS * OPENING_BALANCE
    for wallet in wallets:
        assert wallet.overdraft.allows(eur(balances[wallet.id])), f"{wallet.name} below its floor"
    audit = ReconciliationService(SqlLedgerAudit(session_factory), CLOCK)
    assert (await audit.reconcile(limit=10)).is_clean
