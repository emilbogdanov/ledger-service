import asyncio
from datetime import UTC, datetime

from prometheus_client import CollectorRegistry
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.accounts import AccountService
from ledger.application.outbox import OutboxRelay
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.money import Currency
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from ledger.infrastructure.redis_stream import RedisStreamPublisher
from ledger.worker import OutboxMetrics, OutboxWorker
from tests.integration.clients import ACME
from tests.unit.application.fakes import FixedClock

STREAM = "ledger:events"
CLOCK = FixedClock(datetime(2026, 10, 19, 0, 15, tzinfo=UTC))


async def test_running_worker_publishes_events_as_they_are_committed(
    session_factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    relay = OutboxRelay(
        SqlUnitOfWork(session_factory),
        RedisStreamPublisher(redis, stream=STREAM, max_length=10_000),
        CLOCK,
        batch_size=10,
    )
    stop = asyncio.Event()
    running = asyncio.create_task(
        OutboxWorker(
            relay, OutboxMetrics(CollectorRegistry()), poll_interval=0.05, max_backoff=1
        ).run(stop)
    )
    accounts = AccountService(SqlUnitOfWork(session_factory), CLOCK)

    for number in range(3):
        await accounts.open_account(
            ACME,
            name=AccountName(f"Wallet {number}"),
            currency=Currency.EUR,
            overdraft=OverdraftPolicy.none(),
        )
    async with asyncio.timeout(5):
        while await redis.xlen(STREAM) < 3:
            await asyncio.sleep(0.05)

    stop.set()
    await asyncio.wait_for(running, timeout=5)
    assert await redis.xlen(STREAM) == 3
