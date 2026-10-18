from dataclasses import dataclass
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.infrastructure.db.repositories import (
    SqlAccountRepository,
    SqlClientRepository,
    SqlEntryRepository,
    SqlIdempotencyRecords,
)


@dataclass(frozen=True, slots=True)
class _Transaction:
    session: AsyncSession
    clients: SqlClientRepository
    accounts: SqlAccountRepository
    entries: SqlEntryRepository
    idempotency: SqlIdempotencyRecords


class SqlUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._transaction: _Transaction | None = None

    @property
    def clients(self) -> SqlClientRepository:
        return self._active().clients

    @property
    def accounts(self) -> SqlAccountRepository:
        return self._active().accounts

    @property
    def entries(self) -> SqlEntryRepository:
        return self._active().entries

    @property
    def idempotency(self) -> SqlIdempotencyRecords:
        return self._active().idempotency

    async def __aenter__(self) -> Self:
        if self._transaction is not None:
            raise RuntimeError("unit of work is already in use; blocks can't be nested")
        session = self._session_factory()
        self._transaction = _Transaction(
            session=session,
            clients=SqlClientRepository(session),
            accounts=SqlAccountRepository(session),
            entries=SqlEntryRepository(session),
            idempotency=SqlIdempotencyRecords(session),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        transaction, self._transaction = self._active(), None
        # Closing rolls back whatever was not committed and releases the row locks.
        await transaction.session.close()

    async def commit(self) -> None:
        await self._active().session.commit()

    def _active(self) -> _Transaction:
        if self._transaction is None:
            raise RuntimeError("unit of work used outside 'async with'")
        return self._transaction
