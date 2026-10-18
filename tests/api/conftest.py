from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from ledger.api.app import create_app
from ledger.api.container import Container
from ledger.application.clients import ClientService
from tests.unit.application.fakes import FakeUnitOfWork, FixedClock

type Headers = dict[str, str]

NOW = datetime(2026, 10, 18, 17, 30, tzinfo=UTC)


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
async def http(uow: FakeUnitOfWork) -> AsyncIterator[AsyncClient]:
    # One shared fake stands in for the database across requests.
    container = Container(uow_factory=lambda: uow, clock=FixedClock(NOW))
    transport = ASGITransport(app=create_app(container))
    async with AsyncClient(transport=transport, base_url="http://ledger.test") as client:
        yield client


async def api_key_headers(uow: FakeUnitOfWork, name: str) -> Headers:
    registered = await ClientService(uow).register(name)
    return {"Authorization": f"Bearer {registered.api_key}"}


@pytest.fixture
async def as_acme(uow: FakeUnitOfWork) -> Headers:
    return await api_key_headers(uow, "Acme Payments")


@pytest.fixture
async def as_globex(uow: FakeUnitOfWork) -> Headers:
    return await api_key_headers(uow, "Globex Payroll")
