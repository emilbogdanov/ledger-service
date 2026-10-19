import asyncio
import json
from uuid import uuid4

from httpx import AsyncClient
from prometheus_client.parser import text_string_to_metric_families
from redis.asyncio import Redis

from tests.stack.conftest import Stack

# Every run makes its own accounts and keys, so the tests pass against a stack that already
# holds data from earlier runs.


async def open_account(api: AsyncClient, *, overdraft_limit: int | None) -> str:
    response = await api.post(
        "/accounts",
        json={
            "name": f"Stack test {uuid4().hex[:8]}",
            "currency": "EUR",
            "overdraft_limit": overdraft_limit,
        },
    )
    assert response.status_code == 201, response.text
    account_id: str = response.json()["id"]
    return account_id


async def recently_posted_entries(redis: Redis, stream: str) -> set[str]:
    entry_ids: set[str] = set()
    for _, fields in await redis.xrevrange(stream, count=500) or []:
        assert isinstance(fields, dict)
        if fields["type"] == "entry.posted":
            entry_ids.add(json.loads(str(fields["payload"]))["entry_id"])
    return entry_ids


def metric(text: str, name: str, **labels: str) -> float:
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == name and labels.items() <= sample.labels.items()
    )


async def test_transfer_is_posted_once_and_reaches_the_event_stream(
    api: AsyncClient, redis: Redis, stack: Stack
) -> None:
    bank = await open_account(api, overdraft_limit=None)
    wallet = await open_account(api, overdraft_limit=0)
    body = {
        "source_account_id": bank,
        "destination_account_id": wallet,
        "amount": 4_200,
        "currency": "EUR",
        "memo": "Stack test top-up",
    }
    key = {"Idempotency-Key": f"stack-{uuid4()}"}

    first = await api.post("/transfers", json=body, headers=key)
    retry = await api.post("/transfers", json=body, headers=key)

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    assert (await api.get(f"/accounts/{wallet}")).json()["balance"] == 4_200
    entry_id = first.json()["id"]

    async with asyncio.timeout(15):
        while entry_id not in await recently_posted_entries(redis, stack.events_stream):
            await asyncio.sleep(0.25)


async def test_history_lists_the_transfer(api: AsyncClient) -> None:
    bank = await open_account(api, overdraft_limit=None)
    wallet = await open_account(api, overdraft_limit=0)
    posted = await api.post(
        "/transfers",
        json={
            "source_account_id": bank,
            "destination_account_id": wallet,
            "amount": 150,
            "currency": "EUR",
        },
        headers={"Idempotency-Key": f"stack-{uuid4()}"},
    )

    history = await api.get(f"/accounts/{wallet}/postings")

    assert [posting["entry_id"] for posting in history.json()["postings"]] == [posted.json()["id"]]


async def test_api_and_worker_export_their_metrics(api: AsyncClient, stack: Stack) -> None:
    await open_account(api, overdraft_limit=0)

    api_metrics = (await api.get("/metrics")).text
    assert metric(api_metrics, "ledger_http_requests_total", route="/accounts", status="201") >= 1

    # The account.opened event above is published within one poll interval.
    async with AsyncClient() as client, asyncio.timeout(15):
        while True:
            worker_metrics = (await client.get(stack.worker_metrics_url)).text
            if metric(worker_metrics, "ledger_outbox_events_published_total") >= 1:
                break
            await asyncio.sleep(0.25)
    assert metric(worker_metrics, "ledger_outbox_last_success_timestamp_seconds") > 0
