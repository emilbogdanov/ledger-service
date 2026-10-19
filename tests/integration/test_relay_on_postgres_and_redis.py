import json
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledger.application.accounts import AccountService
from ledger.application.events import Event, PendingEvent
from ledger.application.idempotency import IdempotencyKey
from ledger.application.outbox import OutboxRelay
from ledger.application.postings import PostingService
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId
from ledger.domain.money import Currency, Money
from ledger.infrastructure.db.repositories import SqlOutbox
from ledger.infrastructure.db.tables import OutboxEventRow
from ledger.infrastructure.db.unit_of_work import SqlUnitOfWork
from ledger.infrastructure.redis_stream import RedisStreamPublisher
from tests.integration.clients import ACME
from tests.unit.application.fakes import FixedClock

type Sessions = async_sessionmaker[AsyncSession]

STREAM = "ledger:events"
CLOCK = FixedClock(datetime(2026, 10, 18, 22, 30, tzinfo=UTC))


def stream_relay(sessions: Sessions, redis: Redis, *, batch_size: int = 100) -> OutboxRelay:
    publisher = RedisStreamPublisher(redis, stream=STREAM, max_length=10_000)
    return OutboxRelay(SqlUnitOfWork(sessions), publisher, CLOCK, batch_size=batch_size)


async def open_account(sessions: Sessions, name: str, overdraft: OverdraftPolicy) -> AccountId:
    account = await AccountService(SqlUnitOfWork(sessions), CLOCK).open_account(
        ACME, name=AccountName(name), currency=Currency.EUR, overdraft=overdraft
    )
    return account.id


async def stream_messages(redis: Redis) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for _, fields in await redis.xrange(STREAM) or []:
        assert fields is not None
        messages.append({_text(field): _text(value) for field, value in fields.items()})
    return messages


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def test_committed_events_reach_the_stream_once(
    session_factory: Sessions, redis: Redis
) -> None:
    bank = await open_account(session_factory, "Bank settlement", OverdraftPolicy.unlimited())
    wallet = await open_account(session_factory, "Customer wallet", OverdraftPolicy.none())
    posted = await PostingService(SqlUnitOfWork(session_factory), CLOCK).transfer(
        ACME,
        source=bank,
        destination=wallet,
        amount=Money(5_000, Currency.EUR),
        memo="Top-up",
        idempotency_key=IdempotencyKey("top-up-1"),
    )
    relay = stream_relay(session_factory, redis)

    assert await relay.relay() == 3
    assert await relay.relay() == 0

    messages = await stream_messages(redis)
    assert [message["type"] for message in messages] == [
        "account.opened",
        "account.opened",
        "entry.posted",
    ]
    assert len({message["event_id"] for message in messages}) == 3
    entry_posted = json.loads(messages[-1]["payload"])
    assert entry_posted["entry_id"] == str(posted.entry.id)
    assert entry_posted["postings"][1] == {
        "account_id": str(wallet),
        "amount": 5_000,
        "currency": "EUR",
    }
    assert messages[-1]["occurred_at"] == CLOCK.now().isoformat()

    async with session_factory() as session:
        published = list(await session.scalars(select(OutboxEventRow.published_at)))
    assert published == [CLOCK.now()] * 3


class BrokenPublisher:
    async def publish(self, events: Sequence[PendingEvent]) -> None:
        raise ConnectionError(f"stream unavailable, {len(events)} events not sent")


async def test_events_stay_pending_when_publishing_fails(
    session_factory: Sessions, redis: Redis
) -> None:
    await open_account(session_factory, "Customer wallet", OverdraftPolicy.none())
    broken = OutboxRelay(SqlUnitOfWork(session_factory), BrokenPublisher(), CLOCK, batch_size=10)

    with pytest.raises(ConnectionError):
        await broken.relay()

    assert await stream_relay(session_factory, redis).relay() == 1
    assert len(await stream_messages(redis)) == 1


async def test_two_relays_claim_different_events(session_factory: Sessions) -> None:
    async with session_factory() as session:
        outbox = SqlOutbox(session)
        for number in range(6):
            await outbox.add(Event("account.opened", CLOCK.now(), {"number": number}))
        await session.commit()

    async with session_factory() as first, session_factory() as second:
        claimed_first = await SqlOutbox(first).claim(4)
        claimed_second = await SqlOutbox(second).claim(4)

    first_ids = {event.id for event in claimed_first}
    second_ids = {event.id for event in claimed_second}
    assert (len(first_ids), len(second_ids)) == (4, 2)
    assert first_ids.isdisjoint(second_ids)
    assert [event.event.payload["number"] for event in claimed_first] == [0, 1, 2, 3]
