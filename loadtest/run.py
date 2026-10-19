"""Drives a running ledger with concurrent transfers and writes what it measured.

uv run python -m loadtest.run --api-key lk_... --output loadtest/RESULTS.md
"""

import argparse
import asyncio
import os
import platform
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from httpx import AsyncClient, Limits
from prometheus_client.parser import text_string_to_metric_families

from loadtest.stats import Tally, percentile

WALLET_FLOAT = 100_000
# Transfers stay small next to the float, so most of them can be paid.
MAX_TRANSFER = 2_000


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    # Every transfer pays out of the funding account instead of a random wallet.
    hot_source: bool


SCENARIOS = (
    Scenario(
        "spread",
        "random transfers between wallets; contention only when two pick the same account",
        hot_source=False,
    ),
    Scenario(
        "hot account",
        "every transfer pays out of the one funding account, so its row lock serializes them",
        hot_source=True,
    ),
)


@dataclass(frozen=True, slots=True)
class Ledger:
    funding: str
    wallets: list[str]


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: Scenario
    tally: Tally
    seconds: float

    @property
    def entries_posted(self) -> int:
        return self.tally.outcomes["201"]


class LoadTest:
    def __init__(
        self, api: AsyncClient, *, concurrency: int, duration: float, retry_share: float, seed: int
    ) -> None:
        self._api = api
        self._concurrency = concurrency
        self._duration = duration
        self._retry_share = retry_share
        self._random = random.Random(seed)

    async def open_ledger(self, accounts: int) -> Ledger:
        funding = await self._open_account("Load test funding", overdraft_limit=None)
        wallets = await asyncio.gather(
            *(
                self._open_account(f"Load test wallet {n}", overdraft_limit=0)
                for n in range(accounts)
            )
        )
        await asyncio.gather(*(self._transfer(funding, wallet, WALLET_FLOAT) for wallet in wallets))
        return Ledger(funding, list(wallets))

    async def run(self, scenario: Scenario, ledger: Ledger) -> ScenarioResult:
        tally = Tally()
        deadline = time.perf_counter() + self._duration
        started = time.perf_counter()

        async def keep_sending() -> None:
            while time.perf_counter() < deadline:
                if scenario.hot_source:
                    source, destination = ledger.funding, self._random.choice(ledger.wallets)
                else:
                    source, destination = self._random.sample(ledger.wallets, 2)
                body = self._transfer_body(
                    source, destination, self._random.randint(1, MAX_TRANSFER)
                )
                key = f"load-{uuid4()}"
                await self._send(body, key, tally)
                # A client that timed out and tries again with the same key.
                if self._random.random() < self._retry_share:
                    await self._send(body, key, tally)

        await asyncio.gather(*(keep_sending() for _ in range(self._concurrency)))
        return ScenarioResult(scenario, tally, time.perf_counter() - started)

    async def total_balance(self, ledger: Ledger) -> int:
        balances = await asyncio.gather(
            *(
                self._api.get(f"/accounts/{account}")
                for account in [ledger.funding, *ledger.wallets]
            )
        )
        return sum(int(response.json()["balance"]) for response in balances)

    async def _send(self, body: dict[str, object], key: str, tally: Tally) -> None:
        started = time.perf_counter()
        response = await self._api.post("/transfers", json=body, headers={"Idempotency-Key": key})
        latency_ms = (time.perf_counter() - started) * 1000
        outcome = str(response.status_code)
        if response.is_error:
            outcome += f" {response.json().get('code', 'unknown')}"
        tally.record(outcome, latency_ms)

    async def _open_account(self, name: str, *, overdraft_limit: int | None) -> str:
        response = await self._api.post(
            "/accounts", json={"name": name, "currency": "EUR", "overdraft_limit": overdraft_limit}
        )
        response.raise_for_status()
        account_id: str = response.json()["id"]
        return account_id

    async def _transfer(self, source: str, destination: str, amount: int) -> None:
        response = await self._api.post(
            "/transfers",
            json=self._transfer_body(source, destination, amount),
            headers={"Idempotency-Key": f"load-setup-{uuid4()}"},
        )
        response.raise_for_status()

    @staticmethod
    def _transfer_body(source: str, destination: str, amount: int) -> dict[str, object]:
        return {
            "source_account_id": source,
            "destination_account_id": destination,
            "amount": amount,
            "currency": "EUR",
        }


async def published_events(worker_metrics_url: str) -> float:
    async with AsyncClient(timeout=5) as client:
        text = (await client.get(worker_metrics_url)).text
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == "ledger_outbox_events_published_total"
    )


async def seconds_until_published(
    worker_metrics_url: str, events: int, *, timeout: float
) -> float | None:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if await published_events(worker_metrics_url) >= events:
            return time.perf_counter() - started
        await asyncio.sleep(0.1)
    return None


def report(
    results: Sequence[ScenarioResult],
    *,
    args: argparse.Namespace,
    total_balance: int,
    drain_seconds: float | None,
) -> str:
    lines = [
        "# Load test results",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC with `python -m loadtest.run` against"
        " `docker compose up`: one API process, one worker, Postgres and Redis on the same"
        " machine as the load generator.",
        "",
        f"- Machine: {platform.system()} {platform.release()}, {os.cpu_count()} logical CPUs",
        f"- {args.accounts} wallets, {args.concurrency} concurrent clients,"
        f" {args.duration:g} s per scenario, {args.retry_share:.0%} of transfers retried with"
        " the same key",
        "",
        "| Scenario | Requests | Requests/s | Entries posted/s | p50 ms | p95 ms | p99 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        latencies = result.tally.latencies_ms
        lines.append(
            f"| {result.scenario.name} | {result.tally.requests}"
            f" | {result.tally.requests / result.seconds:.0f}"
            f" | {result.entries_posted / result.seconds:.0f}"
            f" | {percentile(latencies, 0.50):.1f}"
            f" | {percentile(latencies, 0.95):.1f}"
            f" | {percentile(latencies, 0.99):.1f} |"
        )
    lines += ["", "Responses:", ""]
    for result in results:
        outcomes = ", ".join(
            f"{count} x {outcome}" for outcome, count in sorted(result.tally.outcomes.items())
        )
        lines.append(f"- {result.scenario.name} ({result.scenario.description}): {outcomes}")
    lines += [
        "",
        "Checks after the run:",
        "",
        f"- Sum of every balance the test touched: {total_balance} (must be 0)",
        f"- Unexpected responses: {sum(result.tally.unexpected for result in results)} (must be 0)",
        "- Outbox drained "
        + (
            f"{drain_seconds:.1f} s after the last request"
            if drain_seconds is not None
            else "NOT within the timeout"
        ),
        "",
    ]
    return "\n".join(lines)


async def main(args: argparse.Namespace) -> int:
    async with AsyncClient(
        base_url=args.base_url,
        headers={"Authorization": f"Bearer {args.api_key}"},
        timeout=30,
        limits=Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency),
    ) as api:
        load = LoadTest(
            api,
            concurrency=args.concurrency,
            duration=args.duration,
            retry_share=args.retry_share,
            seed=args.seed,
        )
        ledger = await load.open_ledger(args.accounts)
        results = []
        for scenario in SCENARIOS:
            sys.stdout.write(f"running {scenario.name} for {args.duration:g} s\n")
            results.append(await load.run(scenario, ledger))
        total_balance = await load.total_balance(ledger)

    # One account.opened per account and one entry.posted per entry, top-ups included.
    events = 1 + args.accounts * 2 + sum(result.entries_posted for result in results)
    drain_seconds = await seconds_until_published(args.worker_metrics_url, events, timeout=60)
    text = report(results, args=args, total_balance=total_balance, drain_seconds=drain_seconds)
    sys.stdout.write(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    failed = total_balance != 0 or drain_seconds is None or any(r.tally.unexpected for r in results)
    return 1 if failed else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="loadtest", description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("LEDGER_LOAD_API_KEY"),
        help="default: $LEDGER_LOAD_API_KEY",
    )
    parser.add_argument("--worker-metrics-url", default="http://localhost:9100/metrics")
    parser.add_argument("--accounts", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--duration", type=float, default=30, help="seconds per scenario")
    parser.add_argument("--retry-share", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--output", help="also write the report to this Markdown file")
    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error("an API key is required: pass --api-key or set LEDGER_LOAD_API_KEY")
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
