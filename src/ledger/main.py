import logging

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ledger.api.app import create_app
from ledger.api.container import Container
from ledger.config import Settings
from ledger.infrastructure.clock import SystemClock
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork

# asyncpg timeouts, in seconds: fail fast when the database is unreachable or a query hangs,
# instead of holding the request (and a pool connection) open indefinitely.
_CONNECT_TIMEOUT = 5
_QUERY_TIMEOUT = 15


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


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )


def app() -> FastAPI:
    # Run with: uvicorn --factory ledger.main:app
    settings = Settings()
    configure_logging(settings)
    return create_app(build_container(settings))
