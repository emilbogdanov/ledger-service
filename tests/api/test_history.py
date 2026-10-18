import pytest
from httpx import AsyncClient

from tests.api.conftest import Headers


@pytest.fixture
async def wallet(http: AsyncClient, as_acme: Headers) -> str:
    accounts = []
    for name, limit in (("Bank settlement", None), ("Customer wallet", 0)):
        response = await http.post(
            "/accounts",
            json={"name": name, "currency": "EUR", "overdraft_limit": limit},
            headers=as_acme,
        )
        accounts.append(response.json()["id"])
    bank, wallet = accounts
    for number in range(1, 4):
        response = await http.post(
            "/transfers",
            json={
                "source_account_id": bank,
                "destination_account_id": wallet,
                "amount": number * 1_000,
                "currency": "EUR",
                "memo": f"Payout {number}",
            },
            headers={**as_acme, "Idempotency-Key": f"payout-{number}"},
        )
        assert response.status_code == 201, response.text
    wallet_id: str = wallet
    return wallet_id


async def test_history_is_paged_with_an_opaque_cursor(
    http: AsyncClient, as_acme: Headers, wallet: str
) -> None:
    first = await http.get(f"/accounts/{wallet}/postings", params={"limit": 2}, headers=as_acme)
    cursor = first.json()["next_cursor"]
    second = await http.get(
        f"/accounts/{wallet}/postings", params={"limit": 2, "cursor": cursor}, headers=as_acme
    )

    assert first.status_code == second.status_code == 200
    postings = first.json()["postings"] + second.json()["postings"]
    assert len(postings) == 3
    assert {posting["memo"] for posting in postings} == {"Payout 1", "Payout 2", "Payout 3"}
    assert all(posting["currency"] == "EUR" for posting in postings)
    assert sum(posting["amount"] for posting in postings) == 6_000
    assert second.json()["next_cursor"] is None


async def test_invalid_cursor_is_refused(http: AsyncClient, as_acme: Headers, wallet: str) -> None:
    response = await http.get(
        f"/accounts/{wallet}/postings", params={"cursor": "garbage"}, headers=as_acme
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_value"


@pytest.mark.parametrize("limit", [0, 101])
async def test_page_size_outside_the_range_is_refused(
    http: AsyncClient, as_acme: Headers, wallet: str, limit: int
) -> None:
    response = await http.get(
        f"/accounts/{wallet}/postings", params={"limit": limit}, headers=as_acme
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


async def test_another_clients_history_is_not_found(
    http: AsyncClient, as_globex: Headers, wallet: str
) -> None:
    response = await http.get(f"/accounts/{wallet}/postings", headers=as_globex)

    assert response.status_code == 404
