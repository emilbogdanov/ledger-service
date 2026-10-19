import asyncio
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from ledger.worker import OutboxWorker

POLL = 0.001


class ScriptedRelay:
    # Plays back one result per pass, then asks the worker to stop.
    def __init__(self, stop: asyncio.Event, *results: int | Exception) -> None:
        self._stop = stop
        self._results = list(results)
        self.passes = 0

    async def relay(self) -> int:
        self.passes += 1
        result = self._results.pop(0)
        if not self._results:
            self._stop.set()
        if isinstance(result, Exception):
            raise result
        return result


async def run(relay: ScriptedRelay, stop: asyncio.Event, *, max_backoff: float = 1.0) -> None:
    worker = OutboxWorker(relay, poll_interval=POLL, max_backoff=max_backoff)
    await asyncio.wait_for(worker.run(stop), timeout=5)


async def test_worker_keeps_relaying_until_a_pass_finds_nothing() -> None:
    stop = asyncio.Event()
    relay = ScriptedRelay(stop, 100, 100, 37, 0)

    await run(relay, stop)

    assert relay.passes == 4


async def test_worker_backs_off_exponentially_up_to_the_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop = asyncio.Event()
    down = RedisConnectionError("Connection refused")
    relay = ScriptedRelay(stop, down, down, down, down, 5, 0)

    with caplog.at_level(logging.INFO, logger="ledger.worker"):
        await run(relay, stop, max_backoff=0.006)

    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert [message.rsplit(" ", 1)[-1] for message in warnings] == [
        "0.002s",
        "0.004s",
        "0.006s",
        "0.006s",
    ]
    assert caplog.records[-1].getMessage() == (
        "relaying events works again after 4 failed attempts"
    )
    assert relay.passes == 6


async def test_worker_survives_a_database_restart() -> None:
    stop = asyncio.Event()
    relay = ScriptedRelay(stop, OSError("server closed the connection"), TimeoutError(), 0)

    await run(relay, stop)

    assert relay.passes == 3


async def test_bugs_stop_the_worker() -> None:
    stop = asyncio.Event()
    relay = ScriptedRelay(stop, KeyError("payload"), 0)

    with pytest.raises(KeyError):
        await run(relay, stop)


async def test_stop_interrupts_the_idle_wait() -> None:
    stop = asyncio.Event()

    class EmptyOutbox:
        async def relay(self) -> int:
            return 0

    worker = OutboxWorker(EmptyOutbox(), poll_interval=60, max_backoff=60)
    running = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(0.01)
    stop.set()

    await asyncio.wait_for(running, timeout=1)
