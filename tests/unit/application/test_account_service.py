from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ledger.application.accounts import AccountService
from ledger.application.events import account_opened
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.errors import AccountNotFoundError
from ledger.domain.ids import AccountId, ClientId
from ledger.domain.money import Currency, Money
from tests.unit.application.fakes import FakeUnitOfWork, FixedClock

ACME = ClientId(uuid4())
GLOBEX = ClientId(uuid4())
OPENED_AT = datetime(2026, 10, 18, 10, 15, tzinfo=UTC)


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def service(uow: FakeUnitOfWork) -> AccountService:
    return AccountService(uow, FixedClock(OPENED_AT))


async def open_wallet(service: AccountService, client_id: ClientId = ACME) -> AccountId:
    account = await service.open_account(
        client_id,
        name=AccountName("Merchant payouts"),
        currency=Currency.EUR,
        overdraft=OverdraftPolicy.none(),
    )
    return account.id


async def test_opened_account_is_stored_empty(service: AccountService, uow: FakeUnitOfWork) -> None:
    account_id = await open_wallet(service)

    stored = await service.get_account(ACME, account_id)

    assert stored.client_id == ACME
    assert str(stored.name) == "Merchant payouts"
    assert stored.balance == Money.zero(Currency.EUR)
    assert uow.commits == 1


async def test_opened_account_is_announced(service: AccountService, uow: FakeUnitOfWork) -> None:
    account_id = await open_wallet(service)

    stored = await service.get_account(ACME, account_id)
    assert uow.committed.outbox == [account_opened(stored, at=OPENED_AT)]


async def test_unknown_account_is_not_found(service: AccountService) -> None:
    missing = AccountId(uuid4())

    with pytest.raises(AccountNotFoundError) as excinfo:
        await service.get_account(ACME, missing)

    assert excinfo.value.account_id == missing


async def test_another_clients_account_is_not_found(service: AccountService) -> None:
    account_id = await open_wallet(service, GLOBEX)

    with pytest.raises(AccountNotFoundError):
        await service.get_account(ACME, account_id)


async def test_changed_overdraft_is_stored(service: AccountService) -> None:
    account_id = await open_wallet(service)

    await service.change_overdraft(ACME, account_id, OverdraftPolicy.up_to(50_000))

    stored = await service.get_account(ACME, account_id)
    assert stored.overdraft == OverdraftPolicy.up_to(50_000)


async def test_another_clients_overdraft_cannot_be_changed(
    service: AccountService, uow: FakeUnitOfWork
) -> None:
    account_id = await open_wallet(service, GLOBEX)

    with pytest.raises(AccountNotFoundError):
        await service.change_overdraft(ACME, account_id, OverdraftPolicy.unlimited())

    assert (await service.get_account(GLOBEX, account_id)).overdraft == OverdraftPolicy.none()
    assert uow.commits == 1
