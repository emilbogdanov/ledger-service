from uuid import uuid4

import pytest
from httpx import AsyncClient, Response

from tests.api.conftest import NOW, Headers


async def open_account(
    http: AsyncClient, headers: Headers, name: str, *, overdraft_limit: int | None = 0
) -> str:
    response = await http.post(
        "/accounts",
        json={"name": name, "currency": "EUR", "overdraft_limit": overdraft_limit},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    account_id: str = response.json()["id"]
    return account_id


async def balance(http: AsyncClient, headers: Headers, account_id: str) -> int:
    response = await http.get(f"/accounts/{account_id}", headers=headers)
    value: int = response.json()["balance"]
    return value


async def send_transfer(
    http: AsyncClient,
    headers: Headers,
    *,
    source: str,
    destination: str,
    amount: int,
    key: str,
    memo: str = "",
) -> Response:
    return await http.post(
        "/transfers",
        json={
            "source_account_id": source,
            "destination_account_id": destination,
            "amount": amount,
            "currency": "EUR",
            "memo": memo,
        },
        headers={**headers, "Idempotency-Key": key},
    )


@pytest.fixture
async def bank(http: AsyncClient, as_acme: Headers) -> str:
    return await open_account(http, as_acme, "Bank settlement", overdraft_limit=None)


@pytest.fixture
async def wallet(http: AsyncClient, as_acme: Headers, bank: str) -> str:
    wallet = await open_account(http, as_acme, "Customer wallet")
    response = await send_transfer(
        http, as_acme, source=bank, destination=wallet, amount=10_000, key="top-up-1"
    )
    assert response.status_code == 201, response.text
    return wallet


@pytest.fixture
async def merchant(http: AsyncClient, as_acme: Headers) -> str:
    return await open_account(http, as_acme, "Merchant payouts")


async def test_transfer_posts_an_entry(
    http: AsyncClient, as_acme: Headers, wallet: str, merchant: str
) -> None:
    response = await send_transfer(
        http,
        as_acme,
        source=wallet,
        destination=merchant,
        amount=2_499,
        key="order-10432",
        memo="Order 10432",
    )

    assert response.status_code == 201
    entry = response.json()
    assert response.headers["Location"] == f"/entries/{entry['id']}"
    assert "Idempotent-Replayed" not in response.headers
    assert entry == {
        "id": entry["id"],
        "memo": "Order 10432",
        "posted_at": NOW.isoformat().replace("+00:00", "Z"),
        "postings": [
            {"account_id": wallet, "amount": -2_499, "currency": "EUR"},
            {"account_id": merchant, "amount": 2_499, "currency": "EUR"},
        ],
    }
    assert await balance(http, as_acme, wallet) == 7_501
    assert await balance(http, as_acme, merchant) == 2_499


async def test_retried_transfer_returns_the_first_entry(
    http: AsyncClient, as_acme: Headers, wallet: str, merchant: str
) -> None:
    first = await send_transfer(
        http, as_acme, source=wallet, destination=merchant, amount=1_000, key="order-10433"
    )
    retry = await send_transfer(
        http, as_acme, source=wallet, destination=merchant, amount=1_000, key="order-10433"
    )

    assert retry.status_code == 200
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()
    assert await balance(http, as_acme, wallet) == 9_000


async def test_key_reused_for_a_different_transfer_is_refused(
    http: AsyncClient, as_acme: Headers, wallet: str, merchant: str
) -> None:
    await send_transfer(
        http, as_acme, source=wallet, destination=merchant, amount=1_000, key="order-10434"
    )

    response = await send_transfer(
        http, as_acme, source=wallet, destination=merchant, amount=1_200, key="order-10434"
    )

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"


async def test_transfer_needs_an_idempotency_key(
    http: AsyncClient, as_acme: Headers, wallet: str, merchant: str
) -> None:
    response = await http.post(
        "/transfers",
        json={
            "source_account_id": wallet,
            "destination_account_id": merchant,
            "amount": 100,
            "currency": "EUR",
        },
        headers=as_acme,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["errors"][0]["loc"] == ["header", "Idempotency-Key"]


async def test_transfer_beyond_the_balance_is_refused(
    http: AsyncClient, as_acme: Headers, wallet: str, merchant: str
) -> None:
    response = await send_transfer(
        http, as_acme, source=wallet, destination=merchant, amount=10_001, key="order-10435"
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": f"account {wallet} cannot spend 100.01 EUR from a balance of 100.00 EUR",
        "code": "insufficient_funds",
    }
    assert await balance(http, as_acme, wallet) == 10_000


@pytest.mark.parametrize(
    ("change", "status_code", "code"),
    [
        ({"amount": 0}, 422, "invalid_request"),
        ({"amount": -500}, 422, "invalid_request"),
        ({"currency": "USD"}, 422, "currency_mismatch"),
        ({"destination_account_id": str(uuid4())}, 404, "account_not_found"),
        ({"memo": "x" * 501}, 422, "invalid_entry"),
    ],
)
async def test_invalid_transfers_are_refused(
    http: AsyncClient,
    as_acme: Headers,
    wallet: str,
    merchant: str,
    change: dict[str, object],
    status_code: int,
    code: str,
) -> None:
    body = {
        "source_account_id": wallet,
        "destination_account_id": merchant,
        "amount": 100,
        "currency": "EUR",
        **change,
    }

    response = await http.post(
        "/transfers", json=body, headers={**as_acme, "Idempotency-Key": "order-10436"}
    )

    assert response.status_code == status_code, response.text
    assert response.json()["code"] == code


async def test_transfer_from_another_clients_account_is_not_found(
    http: AsyncClient, as_acme: Headers, as_globex: Headers, merchant: str
) -> None:
    theirs = await open_account(http, as_globex, "Globex reserve", overdraft_limit=None)

    response = await send_transfer(
        http, as_acme, source=theirs, destination=merchant, amount=100, key="order-10437"
    )

    assert response.status_code == 404
    assert await balance(http, as_globex, theirs) == 0


async def test_entry_splits_a_payment_and_reads_back(
    http: AsyncClient, as_acme: Headers, wallet: str, merchant: str
) -> None:
    fees = await open_account(http, as_acme, "Card fees")
    legs = [
        {"account_id": wallet, "amount": -5_000, "currency": "EUR"},
        {"account_id": merchant, "amount": 4_855, "currency": "EUR"},
        {"account_id": fees, "amount": 145, "currency": "EUR"},
    ]

    posted = await http.post(
        "/entries",
        json={"postings": legs, "memo": "Order 10438 with card fee"},
        headers={**as_acme, "Idempotency-Key": "order-10438"},
    )
    read = await http.get(posted.headers["Location"], headers=as_acme)

    assert posted.status_code == 201
    assert read.status_code == 200
    assert read.json() == posted.json()
    assert read.json()["postings"] == legs
    assert await balance(http, as_acme, fees) == 145


async def test_unbalanced_entry_is_refused(
    http: AsyncClient, as_acme: Headers, wallet: str, merchant: str
) -> None:
    response = await http.post(
        "/entries",
        json={
            "postings": [
                {"account_id": wallet, "amount": -995, "currency": "EUR"},
                {"account_id": merchant, "amount": 1_000, "currency": "EUR"},
            ]
        },
        headers={**as_acme, "Idempotency-Key": "order-10439"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "postings do not balance: off by 0.05 EUR",
        "code": "unbalanced_entry",
    }


async def test_entry_with_too_many_postings_is_refused(http: AsyncClient, as_acme: Headers) -> None:
    legs = [{"account_id": str(uuid4()), "amount": 1, "currency": "EUR"}] * 51

    response = await http.post(
        "/entries", json={"postings": legs}, headers={**as_acme, "Idempotency-Key": "bulk-1"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


async def test_another_clients_entry_is_not_found(
    http: AsyncClient, as_acme: Headers, as_globex: Headers, wallet: str, merchant: str
) -> None:
    posted = await send_transfer(
        http, as_acme, source=wallet, destination=merchant, amount=100, key="order-10440"
    )

    response = await http.get(posted.headers["Location"], headers=as_globex)

    assert response.status_code == 404
    assert response.json()["code"] == "entry_not_found"
