from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.api.app import create_app
from ledger.api.container import Container
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from tests.unit.application.fakes import FixedClock


@pytest.fixture
async def http(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    container = Container(
        uow_factory=lambda: SqlUnitOfWork(session_factory),
        clock=FixedClock(datetime(2026, 10, 18, 18, 0, tzinfo=UTC)),
    )
    registered = await container.clients().register("Initech Billing")
    transport = ASGITransport(app=create_app(container))
    async with AsyncClient(
        transport=transport,
        base_url="http://ledger.test",
        headers={"Authorization": f"Bearer {registered.api_key}"},
    ) as client:
        yield client


async def open_account(http: AsyncClient, name: str, overdraft_limit: int | None) -> str:
    response = await http.post(
        "/accounts",
        json={"name": name, "currency": "EUR", "overdraft_limit": overdraft_limit},
    )
    assert response.status_code == 201, response.text
    account_id: str = response.json()["id"]
    return account_id


async def test_transfer_round_trip_through_http_and_postgres(http: AsyncClient) -> None:
    bank = await open_account(http, "Bank settlement", None)
    wallet = await open_account(http, "Customer wallet", 0)
    body = {
        "source_account_id": bank,
        "destination_account_id": wallet,
        "amount": 12_500,
        "currency": "EUR",
        "memo": "Top-up",
    }

    first = await http.post("/transfers", json=body, headers={"Idempotency-Key": "top-up-7"})
    retry = await http.post("/transfers", json=body, headers={"Idempotency-Key": "top-up-7"})
    entry = await http.get(first.headers["Location"])

    assert (first.status_code, retry.status_code, entry.status_code) == (201, 200, 200)
    assert retry.json() == entry.json() == first.json()
    assert (await http.get(f"/accounts/{wallet}")).json()["balance"] == 12_500
    assert (await http.get(f"/accounts/{bank}")).json()["balance"] == -12_500


async def test_wrong_key_is_refused_by_the_real_client_store(http: AsyncClient) -> None:
    response = await http.get("/health", headers={"Authorization": "Bearer lk_unknown"})
    assert response.status_code == 200

    response = await http.post(
        "/accounts",
        json={"name": "Wallet", "currency": "EUR"},
        headers={"Authorization": "Bearer lk_unknown"},
    )
    assert response.status_code == 401
