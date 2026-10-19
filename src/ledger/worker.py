import asyncio
import logging
from contextlib import suppress
from typing import Final, Protocol

from prometheus_client import CollectorRegistry, Counter, Gauge
from redis.exceptions import RedisError
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# What a database or Redis that is down, restarting or slow looks like. Anything else is a bug:
# it stops the worker so the process manager restarts it and someone notices.
_TRANSIENT_ERRORS: Final = (OSError, TimeoutError, RedisError, DBAPIError)


class Relay(Protocol):
    async def relay(self) -> int: ...


class OutboxMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.published = Counter(
            "ledger_outbox_events_published",
            "Events sent to the stream; a batch resent after a failure counts again.",
            registry=registry,
        )
        self.failures = Counter(
            "ledger_outbox_relay_failures",
            "Relay passes that failed because Redis or Postgres was unavailable.",
            registry=registry,
        )
        # Alert on its age rather than on failures: it also goes stale when the worker hangs
        # or isn't running at all, which no failure counter would show.
        self.last_success = Gauge(
            "ledger_outbox_last_success_timestamp_seconds",
            "When a relay pass last completed, sending events or finding none to send.",
            registry=registry,
        )


class OutboxWorker:
    def __init__(
        self,
        relay: Relay,
        metrics: OutboxMetrics,
        *,
        poll_interval: float,
        max_backoff: float,
    ) -> None:
        self._relay = relay
        self._metrics = metrics
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff

    async def run(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                sent = await self._relay.relay()
            except _TRANSIENT_ERRORS:
                failures += 1
                self._metrics.failures.inc()
                delay = min(self._poll_interval * 2**failures, self._max_backoff)
                logger.warning(
                    "relaying events failed (attempt %d), retrying in %.3fs",
                    failures,
                    delay,
                    exc_info=True,
                )
                await _wait(stop, delay)
                continue
            self._metrics.published.inc(sent)
            self._metrics.last_success.set_to_current_time()
            if failures:
                logger.info("relaying events works again after %d failed attempts", failures)
                failures = 0
            # A pass that sent something may have left more behind, so only an empty one waits.
            if sent == 0:
                await _wait(stop, self._poll_interval)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
