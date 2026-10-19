from ledger.application.ports import Clock, EventPublisher, UnitOfWork


class OutboxRelay:
    def __init__(
        self, uow: UnitOfWork, publisher: EventPublisher, clock: Clock, *, batch_size: int
    ) -> None:
        self._uow = uow
        self._publisher = publisher
        self._clock = clock
        self._batch_size = batch_size

    async def relay(self) -> int:
        async with self._uow as uow:
            pending = await uow.outbox.claim(self._batch_size)
            if not pending:
                return 0
            # Publish first, mark second. If the commit fails after publishing, the events go
            # out again on the next pass, which consumers must handle anyway since delivery is
            # at least once. The other order would lose events when publishing fails.
            await self._publisher.publish(pending)
            await uow.outbox.mark_published([event.id for event in pending], at=self._clock.now())
            await uow.commit()
        return len(pending)
