from uuid import uuid4

import pytest

from ledger.application.clients import API_KEY_PREFIX, Client, ClientService, hash_api_key
from ledger.application.errors import InvalidApiKeyError
from ledger.domain.errors import InvalidValueError
from ledger.domain.ids import ClientId
from tests.unit.application.fakes import FakeUnitOfWork


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def service(uow: FakeUnitOfWork) -> ClientService:
    return ClientService(uow)


async def test_registered_client_authenticates_with_its_key(service: ClientService) -> None:
    registered = await service.register("Acme Payments")

    assert await service.authenticate(registered.api_key) == registered.client
    assert registered.client.name == "Acme Payments"


async def test_api_key_is_prefixed_and_only_its_hash_is_stored(
    service: ClientService, uow: FakeUnitOfWork
) -> None:
    registered = await service.register("Acme Payments")

    assert registered.api_key.startswith(API_KEY_PREFIX)
    assert list(uow.committed.clients) == [hash_api_key(registered.api_key)]


async def test_every_client_gets_its_own_key(service: ClientService) -> None:
    first = await service.register("Acme Payments")
    second = await service.register("Globex Payroll")

    assert first.api_key != second.api_key
    assert (await service.authenticate(second.api_key)).id == second.client.id


@pytest.mark.parametrize("api_key", ["", "lk_not-a-real-key"])
async def test_unknown_key_is_refused(service: ClientService, api_key: str) -> None:
    await service.register("Acme Payments")

    with pytest.raises(InvalidApiKeyError):
        await service.authenticate(api_key)


@pytest.mark.parametrize(
    ("name", "message"),
    [("", "must not be blank"), ("  ", "must not be blank"), ("x" * 101, "at most 100")],
)
def test_rejects_invalid_client_names(name: str, message: str) -> None:
    with pytest.raises(InvalidValueError, match=message):
        Client(ClientId(uuid4()), name)
