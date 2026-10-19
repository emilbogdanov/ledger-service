import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis


# These tests drive a running stack (docker compose up) from outside, the way a client would.
# The stack CI job sets LEDGER_STACK_API_KEY after creating a client; without it they skip.
@dataclass(frozen=True, slots=True)
class Stack:
    api_url: str
    api_key: str
    worker_metrics_url: str
    redis_url: str
    events_stream: str


@pytest.fixture(scope="session")
def stack() -> Stack:
    api_key = os.environ.get("LEDGER_STACK_API_KEY")
    if not api_key:
        pytest.skip("set LEDGER_STACK_API_KEY to test a running stack")
    return Stack(
        api_url=os.environ.get("LEDGER_STACK_API_URL", "http://localhost:8000"),
        api_key=api_key,
        worker_metrics_url=os.environ.get(
            "LEDGER_STACK_WORKER_METRICS_URL", "http://localhost:9100/metrics"
        ),
        redis_url=os.environ.get("LEDGER_STACK_REDIS_URL", "redis://localhost:6379/0"),
        events_stream=os.environ.get("LEDGER_STACK_EVENTS_STREAM", "ledger:events"),
    )


@pytest.fixture
async def api(stack: Stack) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        base_url=stack.api_url,
        headers={"Authorization": f"Bearer {stack.api_key}"},
        timeout=10,
    ) as client:
        yield client


@pytest.fixture
async def redis(stack: Stack) -> AsyncIterator[Redis]:
    client = Redis.from_url(stack.redis_url, decode_responses=True)
    yield client
    await client.aclose()
