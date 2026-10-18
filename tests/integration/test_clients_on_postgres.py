from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.clients import ClientService
from ledger.application.errors import InvalidApiKeyError
from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, ClientId
from ledger.domain.money import Currency
from ledger.infrastructure.db.repositories import SqlAccountRepository
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork

type Sessions = async_sessionmaker[AsyncSession]


async def test_registered_client_authenticates_by_key(session_factory: Sessions) -> None:
    clients = ClientService(SqlUnitOfWork(session_factory))
    registered = await clients.register("Initech Billing")

    assert await clients.authenticate(registered.api_key) == registered.client
    with pytest.raises(InvalidApiKeyError):
        await clients.authenticate(registered.api_key + "x")


async def test_account_of_an_unregistered_client_is_refused(session_factory: Sessions) -> None:
    stray = Account.open(
        account_id=AccountId(uuid4()),
        client_id=ClientId(uuid4()),
        name=AccountName("Orphan"),
        currency=Currency.EUR,
        overdraft=OverdraftPolicy.none(),
    )

    async with session_factory() as session:
        await SqlAccountRepository(session).add(stray)
        with pytest.raises(IntegrityError, match="fk_accounts_client_id_clients"):
            await session.commit()
