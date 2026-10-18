from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, ClientId
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.repositories import SqlAccountRepository
from tests.integration.clients import ACME, GLOBEX

type Sessions = async_sessionmaker[AsyncSession]


def account(
    *,
    client_id: ClientId = ACME,
    currency: Currency = Currency.EUR,
    overdraft: OverdraftPolicy | None = None,
) -> Account:
    return Account.open(
        account_id=AccountId(uuid4()),
        client_id=client_id,
        name=AccountName("Merchant payouts"),
        currency=currency,
        overdraft=overdraft or OverdraftPolicy.none(),
    )


async def store(sessions: Sessions, *accounts: Account) -> None:
    async with sessions() as session:
        repository = SqlAccountRepository(session)
        for stored in accounts:
            await repository.add(stored)
        await session.commit()


async def test_account_reads_back_as_stored(session_factory: Sessions) -> None:
    payouts = account(currency=Currency.KWD, overdraft=OverdraftPolicy.up_to(250_000))
    await store(session_factory, payouts)

    async with session_factory() as session:
        found = await SqlAccountRepository(session).get(ACME, payouts.id)

    assert found is not None
    assert (found.id, found.client_id, found.name) == (payouts.id, ACME, payouts.name)
    assert found.currency is Currency.KWD
    assert found.overdraft == OverdraftPolicy.up_to(250_000)
    assert found.balance == Money(0, Currency.KWD)


async def test_unlimited_overdraft_survives_the_round_trip(session_factory: Sessions) -> None:
    settlement = account(overdraft=OverdraftPolicy.unlimited())
    await store(session_factory, settlement)

    async with session_factory() as session:
        found = await SqlAccountRepository(session).get(ACME, settlement.id)

    assert found is not None
    assert found.overdraft == OverdraftPolicy.unlimited()


async def test_another_clients_account_is_invisible(session_factory: Sessions) -> None:
    theirs = account(client_id=GLOBEX)
    await store(session_factory, theirs)

    async with session_factory() as session:
        repository = SqlAccountRepository(session)
        assert await repository.get(ACME, theirs.id) is None
        assert await repository.lock(ACME, [theirs.id]) == {}


async def test_lock_returns_only_the_clients_existing_accounts(session_factory: Sessions) -> None:
    ours, theirs = account(), account(client_id=GLOBEX)
    await store(session_factory, ours, theirs)

    async with session_factory() as session:
        locked = await SqlAccountRepository(session).lock(
            ACME, [ours.id, theirs.id, AccountId(uuid4())]
        )

    assert list(locked) == [ours.id]


async def test_lock_with_no_ids_returns_nothing(session_factory: Sessions) -> None:
    async with session_factory() as session:
        assert await SqlAccountRepository(session).lock(ACME, []) == {}


async def test_updated_balance_and_overdraft_are_stored(session_factory: Sessions) -> None:
    wallet = account(overdraft=OverdraftPolicy.unlimited())
    await store(session_factory, wallet)

    async with session_factory() as session:
        repository = SqlAccountRepository(session)
        locked = (await repository.lock(ACME, [wallet.id]))[wallet.id]
        locked.post(Money(-7_250, Currency.EUR))
        locked.change_overdraft(OverdraftPolicy.up_to(10_000))
        await repository.update(locked)
        await session.commit()

    async with session_factory() as session:
        found = await SqlAccountRepository(session).get(ACME, wallet.id)

    assert found is not None
    assert found.balance == Money(-7_250, Currency.EUR)
    assert found.overdraft == OverdraftPolicy.up_to(10_000)


async def test_updating_an_account_that_was_not_locked_is_refused(
    session_factory: Sessions,
) -> None:
    wallet = account()
    await store(session_factory, wallet)

    async with session_factory() as session:
        repository = SqlAccountRepository(session)
        read = await repository.get(ACME, wallet.id)
        assert read is not None

        with pytest.raises(RuntimeError, match="must be locked"):
            await repository.update(read)


async def test_locked_account_stays_locked_until_the_transaction_ends(
    session_factory: Sessions,
) -> None:
    wallet = account()
    await store(session_factory, wallet)

    async with session_factory() as holder, session_factory() as waiter:
        await SqlAccountRepository(holder).lock(ACME, [wallet.id])
        await waiter.execute(text("SET LOCAL lock_timeout = '200ms'"))

        with pytest.raises(DBAPIError, match="lock timeout"):
            await SqlAccountRepository(waiter).lock(ACME, [wallet.id])

        await holder.rollback()
        await waiter.rollback()
        assert list(await SqlAccountRepository(waiter).lock(ACME, [wallet.id])) == [wallet.id]
