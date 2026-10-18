from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.repositories import SqlAccountRepository, SqlEntryRepository
from tests.integration.clients import ACME

type Sessions = async_sessionmaker[AsyncSession]

POSTED_AT = datetime(2026, 10, 18, 11, 42, 7, 125_000, tzinfo=UTC)


async def open_accounts(sessions: Sessions, *currencies: Currency) -> list[AccountId]:
    accounts = [
        Account.open(
            account_id=AccountId(uuid4()),
            client_id=ACME,
            name=AccountName(f"{currency} clearing"),
            currency=currency,
            overdraft=OverdraftPolicy.unlimited(),
        )
        for currency in currencies
    ]
    async with sessions() as session:
        repository = SqlAccountRepository(session)
        for account in accounts:
            await repository.add(account)
        await session.commit()
    return [account.id for account in accounts]


async def store(sessions: Sessions, entry: JournalEntry) -> None:
    async with sessions() as session:
        await SqlEntryRepository(session).add(entry)
        await session.commit()


def entry(*postings: Posting, memo: str = "Payout batch 2026-10-18") -> JournalEntry:
    return JournalEntry(
        id=EntryId(uuid4()), client_id=ACME, postings=postings, memo=memo, posted_at=POSTED_AT
    )


async def test_entry_reads_back_with_its_postings_in_order(session_factory: Sessions) -> None:
    wallet, merchant, fees = await open_accounts(session_factory, *[Currency.EUR] * 3)
    stored = entry(
        Posting(merchant, Money(4_855, Currency.EUR)),
        Posting(wallet, Money(-5_000, Currency.EUR)),
        Posting(fees, Money(145, Currency.EUR)),
    )
    await store(session_factory, stored)

    async with session_factory() as session:
        found = await SqlEntryRepository(session).get(ACME, stored.id)

    assert found == stored


async def test_multi_currency_entry_keeps_each_postings_currency(
    session_factory: Sessions,
) -> None:
    eur_wallet, eur_fx, jpy_fx, jpy_wallet = await open_accounts(
        session_factory, Currency.EUR, Currency.EUR, Currency.JPY, Currency.JPY
    )
    stored = entry(
        Posting(eur_wallet, Money(-10_000, Currency.EUR)),
        Posting(eur_fx, Money(10_000, Currency.EUR)),
        Posting(jpy_fx, Money(-17_350, Currency.JPY)),
        Posting(jpy_wallet, Money(17_350, Currency.JPY)),
        memo="Convert 100.00 EUR to JPY",
    )
    await store(session_factory, stored)

    async with session_factory() as session:
        found = await SqlEntryRepository(session).get(ACME, stored.id)

    assert found == stored


async def test_entries_of_other_clients_and_unknown_ids_are_not_found(
    session_factory: Sessions,
) -> None:
    payer, payee = await open_accounts(session_factory, Currency.EUR, Currency.EUR)
    stored = entry(Posting(payer, Money(-1, Currency.EUR)), Posting(payee, Money(1, Currency.EUR)))
    await store(session_factory, stored)

    async with session_factory() as session:
        repository = SqlEntryRepository(session)
        assert await repository.get(ClientId(uuid4()), stored.id) is None
        assert await repository.get(ACME, EntryId(uuid4())) is None
