from uuid import uuid4

import pytest
from httpx import AsyncClient

from tests.api.conftest import Headers


async def open_account(http: AsyncClient, headers: Headers, **fields: object) -> dict[str, object]:
    response = await http.post(
        "/accounts", json={"name": "Customer wallet", "currency": "EUR", **fields}, headers=headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def test_opened_account_starts_empty_without_overdraft(
    http: AsyncClient, as_acme: Headers
) -> None:
    response = await http.post(
        "/accounts", json={"name": "Customer wallet", "currency": "EUR"}, headers=as_acme
    )

    assert response.status_code == 201
    body = response.json()
    assert response.headers["Location"] == f"/accounts/{body['id']}"
    assert body == {
        "id": body["id"],
        "name": "Customer wallet",
        "currency": "EUR",
        "balance": 0,
        "overdraft_limit": 0,
    }


async def test_account_reads_back_as_opened(http: AsyncClient, as_acme: Headers) -> None:
    opened = await open_account(http, as_acme, currency="JPY", overdraft_limit=None)

    response = await http.get(f"/accounts/{opened['id']}", headers=as_acme)

    assert response.status_code == 200
    assert response.json() == opened
    assert opened["overdraft_limit"] is None


async def test_overdraft_limit_can_be_changed(http: AsyncClient, as_acme: Headers) -> None:
    opened = await open_account(http, as_acme)

    response = await http.patch(
        f"/accounts/{opened['id']}", json={"overdraft_limit": 25_000}, headers=as_acme
    )

    assert response.status_code == 200
    assert response.json()["overdraft_limit"] == 25_000
    reread = await http.get(f"/accounts/{opened['id']}", headers=as_acme)
    assert reread.json()["overdraft_limit"] == 25_000


async def test_another_clients_account_is_not_found(
    http: AsyncClient, as_acme: Headers, as_globex: Headers
) -> None:
    theirs = await open_account(http, as_globex)

    read = await http.get(f"/accounts/{theirs['id']}", headers=as_acme)
    changed = await http.patch(
        f"/accounts/{theirs['id']}", json={"overdraft_limit": None}, headers=as_acme
    )

    assert read.status_code == changed.status_code == 404
    assert read.json()["code"] == "account_not_found"


async def test_unknown_account_is_not_found(http: AsyncClient, as_acme: Headers) -> None:
    response = await http.get(f"/accounts/{uuid4()}", headers=as_acme)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"name": "  "}, "invalid_value"),
        ({"name": "x" * 121}, "invalid_value"),
        ({"currency": "XYZ"}, "invalid_request"),
        ({"overdraft_limit": -1}, "invalid_request"),
        ({"overdraft": 100}, "invalid_request"),
    ],
)
async def test_invalid_accounts_are_refused(
    http: AsyncClient, as_acme: Headers, fields: dict[str, object], code: str
) -> None:
    response = await http.post(
        "/accounts", json={"name": "Customer wallet", "currency": "EUR", **fields}, headers=as_acme
    )

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_malformed_account_id_is_a_validation_error(
    http: AsyncClient, as_acme: Headers
) -> None:
    response = await http.get("/accounts/not-a-uuid", headers=as_acme)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
