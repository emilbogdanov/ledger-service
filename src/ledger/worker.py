import asyncio
import logging
from contextlib import suppress
from typing import Final, Protocol

from redis.exceptions import RedisError
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# What a database or Redis that is down, restarting or slow looks like. Anything else is a bug:
# it stops the worker so the process manager restarts it and someone notices.
_TRANSIENT_ERRORS: Final = (OSError, TimeoutError, RedisError, DBAPIError)


class Relay(Protocol):
    async def relay(self) -> int: ...


class OutboxWorker:
    def __init__(self, relay: Relay, *, poll_interval: float, max_backoff: float) -> None:
        self._relay = relay
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff

    async def run(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                sent = await self._relay.relay()
            except _TRANSIENT_ERRORS:
                failures += 1
                delay = min(self._poll_interval * 2**failures, self._max_backoff)
                logger.warning(
                    "relaying events failed (attempt %d), retrying in %.3fs",
                    failures,
                    delay,
                    exc_info=True,
                )
                await _wait(stop, delay)
                continue
            if failures:
                logger.info("relaying events works again after %d failed attempts", failures)
                failures = 0
            # A pass that sent something may have left more behind, so only an empty one waits.
            if sent == 0:
                await _wait(stop, self._poll_interval)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
