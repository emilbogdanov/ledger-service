import json
from collections.abc import Sequence

from redis.asyncio import Redis

from ledger.application.events import PendingEvent


class RedisStreamPublisher:
    def __init__(self, redis: Redis, *, stream: str, max_length: int) -> None:
        self._redis = redis
        self._stream = stream
        self._max_length = max_length

    async def publish(self, events: Sequence[PendingEvent]) -> None:
        # One round trip for the whole batch. Not a MULTI transaction: a partial batch is
        # simply sent again, and consumers already have to tolerate duplicates.
        async with self._redis.pipeline(transaction=False) as pipeline:
            for pending in events:
                pipeline.xadd(
                    self._stream,
                    {
                        "event_id": str(pending.id),
                        "type": pending.event.type,
                        "occurred_at": pending.event.occurred_at.isoformat(),
                        "payload": json.dumps(pending.event.payload, separators=(",", ":")),
                    },
                    # Approximate trimming keeps XADD cheap; the stream is a delivery channel,
                    # not the record. The ledger tables are.
                    maxlen=self._max_length,
                    approximate=True,
                )
            await pipeline.execute()
