from uuid import uuid4

from httpx import AsyncClient
from prometheus_client.parser import text_string_to_metric_families

from tests.api.conftest import Headers


async def scrape(http: AsyncClient) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    response = await http.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    }


def value(
    samples: dict[tuple[str, frozenset[tuple[str, str]]], float], name: str, **labels: str
) -> float:
    return samples.get((name, frozenset(labels.items())), 0.0)


async def open_account(http: AsyncClient, headers: Headers, overdraft_limit: int | None) -> str:
    response = await http.post(
        "/accounts",
        json={"name": "Wallet", "currency": "EUR", "overdraft_limit": overdraft_limit},
        headers=headers,
    )
    account_id: str = response.json()["id"]
    return account_id


async def test_requests_are_counted_by_route_template_and_status(
    http: AsyncClient, as_acme: Headers
) -> None:
    for _ in range(3):
        await http.get(f"/accounts/{uuid4()}", headers=as_acme)

    samples = await scrape(http)

    assert (
        value(
            samples,
            "ledger_http_requests_total",
            method="GET",
            route="/accounts/{account_id}",
            status="404",
        )
        == 3
    )
    assert (
        value(
            samples,
            "ledger_http_request_duration_seconds_count",
            method="GET",
            route="/accounts/{account_id}",
        )
        == 3
    )


async def test_entries_are_counted_as_posted_or_replayed(
    http: AsyncClient, as_acme: Headers
) -> None:
    bank, wallet = await open_account(http, as_acme, None), await open_account(http, as_acme, 0)
    body = {
        "source_account_id": bank,
        "destination_account_id": wallet,
        "amount": 500,
        "currency": "EUR",
    }
    for _ in range(3):
        await http.post("/transfers", json=body, headers={**as_acme, "Idempotency-Key": "t-1"})

    samples = await scrape(http)

    assert value(samples, "ledger_entries_total", outcome="posted") == 1
    assert value(samples, "ledger_entries_total", outcome="replayed") == 2


async def test_refusals_are_counted_by_error_code(http: AsyncClient, as_acme: Headers) -> None:
    await http.get(f"/accounts/{uuid4()}")
    await http.get(f"/accounts/{uuid4()}", headers=as_acme)
    await http.post("/accounts", json={"currency": "EUR"}, headers=as_acme)

    samples = await scrape(http)

    assert value(samples, "ledger_errors_total", code="invalid_api_key") == 1
    assert value(samples, "ledger_errors_total", code="account_not_found") == 1
    assert value(samples, "ledger_errors_total", code="invalid_request") == 1


async def test_scrapes_and_health_checks_are_not_counted(http: AsyncClient) -> None:
    await http.get("/health")
    await scrape(http)

    samples = await scrape(http)

    assert not any(name == "ledger_http_requests_total" for name, _ in samples)
