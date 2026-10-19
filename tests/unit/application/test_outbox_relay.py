from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from ledger.application.events import Event, PendingEvent
from ledger.application.outbox import OutboxRelay
from tests.unit.application.fakes import FakeUnitOfWork, FixedClock

NOW = datetime(2026, 10, 18, 22, 0, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self) -> None:
        self.batches: list[list[int]] = []
        self.failure: Exception | None = None

    async def publish(self, events: Sequence[PendingEvent]) -> None:
        if self.failure is not None:
            raise self.failure
        self.batches.append([pending.id for pending in events])


class StreamUnavailableError(Exception):
    pass


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.committed.outbox = [
        Event("entry.posted", NOW, {"entry_id": f"entry-{number}"}) for number in range(1, 6)
    ]
    return uow


def relay(uow: FakeUnitOfWork, publisher: RecordingPublisher, batch_size: int = 2) -> OutboxRelay:
    return OutboxRelay(uow, publisher, FixedClock(NOW), batch_size=batch_size)


async def test_relay_sends_the_oldest_batch_and_marks_it_published(
    uow: FakeUnitOfWork, publisher: RecordingPublisher
) -> None:
    sent = await relay(uow, publisher).relay()

    assert sent == 2
    assert publisher.batches == [[1, 2]]
    assert uow.committed.published == {1: NOW, 2: NOW}


async def test_successive_passes_drain_the_outbox_once(
    uow: FakeUnitOfWork, publisher: RecordingPublisher
) -> None:
    outbox = relay(uow, publisher)

    sent = [await outbox.relay() for _ in range(4)]

    assert sent == [2, 2, 1, 0]
    assert publisher.batches == [[1, 2], [3, 4], [5]]


async def test_failed_publish_leaves_the_batch_pending(
    uow: FakeUnitOfWork, publisher: RecordingPublisher
) -> None:
    publisher.failure = StreamUnavailableError("connection refused")

    with pytest.raises(StreamUnavailableError):
        await relay(uow, publisher).relay()

    assert uow.committed.published == {}
    publisher.failure = None
    assert await relay(uow, publisher).relay() == 2
    assert publisher.batches == [[1, 2]]


async def test_empty_outbox_publishes_nothing(publisher: RecordingPublisher) -> None:
    assert await relay(FakeUnitOfWork(), publisher).relay() == 0
    assert publisher.batches == []
