import asyncio
import signal
from contextlib import suppress

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ledger.api.app import create_app
from ledger.api.container import Container
from ledger.application.outbox import OutboxRelay
from ledger.application.reconciliation import ReconciliationReport, ReconciliationService
from ledger.config import Settings
from ledger.infrastructure.clock import SystemClock
from ledger.infrastructure.db.audit import SqlLedgerAudit
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from ledger.infrastructure.redis_stream import RedisStreamPublisher
from ledger.logs import configure_logging
from ledger.worker import OutboxWorker

# asyncpg timeouts, in seconds: fail fast when the database is unreachable or a query hangs,
# instead of holding the request (and a pool connection) open indefinitely.
_CONNECT_TIMEOUT = 5
_QUERY_TIMEOUT = 15
# Reconciliation aggregates every posting, which on a large ledger outlasts any request.
_RECONCILIATION_QUERY_TIMEOUT = 30 * 60


def build_container(settings: Settings) -> Container:
    engine = create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        connect_args={"timeout": _CONNECT_TIMEOUT, "command_timeout": _QUERY_TIMEOUT},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return Container(
        uow_factory=lambda: SqlUnitOfWork(session_factory),
        clock=SystemClock(),
        close=engine.dispose,
    )


def app() -> FastAPI:
    # Run with: uvicorn --factory ledger.main:app
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    return create_app(build_container(settings))


async def run_worker(settings: Settings) -> None:
    container = build_container(settings)
    redis = Redis.from_url(
        str(settings.redis_url),
        socket_connect_timeout=_CONNECT_TIMEOUT,
        socket_timeout=_QUERY_TIMEOUT,
    )
    relay = OutboxRelay(
        container.uow_factory(),
        RedisStreamPublisher(
            redis, stream=settings.events_stream, max_length=settings.events_stream_max_length
        ),
        container.clock,
        batch_size=settings.outbox_batch_size,
    )
    worker = OutboxWorker(
        relay,
        poll_interval=settings.outbox_poll_interval_seconds,
        max_backoff=settings.outbox_max_backoff_seconds,
    )
    stop = asyncio.Event()
    # Docker stops a container with SIGTERM. Finishing the current pass first means a batch is
    # never cut off between being published and being marked as published.
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        # Windows has no signal handlers in asyncio; Ctrl+C arrives as KeyboardInterrupt there.
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    try:
        await worker.run(stop)
    finally:
        await redis.aclose()
        await container.close()


async def reconcile(settings: Settings, *, limit: int) -> ReconciliationReport:
    engine = create_async_engine(
        str(settings.database_url),
        connect_args={
            "timeout": _CONNECT_TIMEOUT,
            "command_timeout": _RECONCILIATION_QUERY_TIMEOUT,
        },
    )
    try:
        audit = SqlLedgerAudit(async_sessionmaker(engine, expire_on_commit=False))
        return await ReconciliationService(audit, SystemClock()).reconcile(limit=limit)
    finally:
        await engine.dispose()
