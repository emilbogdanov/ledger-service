import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from contextlib import suppress
from typing import Final

from ledger.api.app import openapi_schema
from ledger.application.clients import RegisteredClient
from ledger.application.reconciliation import ReconciliationReport
from ledger.config import Settings
from ledger.domain.errors import InvalidValueError
from ledger.domain.money import format_minor_units
from ledger.logs import configure_logging
from ledger.main import build_container, reconcile, run_worker

# Distinct from 1, which means the command itself failed, so a scheduler can alert on a ledger
# that doesn't add up without confusing it with a database that was unreachable.
DISCREPANCIES_FOUND: Final = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    commands = parser.add_subparsers(dest="command", required=True)
    create_client = commands.add_parser(
        "create-client",
        help="register an API client and print its key",
        description="The key is printed once and only its hash is stored; keep it safe.",
    )
    create_client.add_argument("--name", required=True, help="who the client is, e.g. a service")
    commands.add_parser(
        "openapi", help="print the API schema as JSON, e.g. to generate a typed client"
    )
    commands.add_parser("worker", help="publish outbox events to the Redis stream until stopped")
    reconcile_command = commands.add_parser(
        "reconcile",
        help="check that every balance matches its postings and every entry balances",
        description=(
            "Safe to run while the API is serving traffic: it reads one consistent snapshot. "
            f"Exits 0 when the ledger reconciles and {DISCREPANCIES_FOUND} when it doesn't."
        ),
    )
    reconcile_command.add_argument(
        "--limit",
        type=_positive_int,
        default=100,
        help="report at most this many findings of each kind (default: 100)",
    )
    args = parser.parse_args(argv)

    match args.command:
        case "create-client":
            return _create_client(args.name)
        case "openapi":
            return _print_openapi()
        case "reconcile":
            return _reconcile(args.limit)
        case _:
            return _worker()


def _create_client(name: str) -> int:
    try:
        registered = asyncio.run(_register(name))
    except InvalidValueError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    sys.stdout.write(
        f"created client {registered.client.name} ({registered.client.id})\n"
        f"api key: {registered.api_key}\n"
    )
    return 0


async def _register(name: str) -> RegisteredClient:
    container = build_container(Settings())
    try:
        return await container.clients().register(name)
    finally:
        await container.close()


def _print_openapi() -> int:
    # ASCII-only output survives a shell redirect on any platform's console encoding.
    sys.stdout.write(json.dumps(openapi_schema(), indent=2, ensure_ascii=True) + "\n")
    return 0


def _worker() -> int:
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    # Ctrl+C on Windows, where asyncio can't install signal handlers, arrives as this.
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker(settings))
    return 0


def _reconcile(limit: int) -> int:
    report = asyncio.run(reconcile(Settings(), limit=limit))
    sys.stdout.write("".join(f"{line}\n" for line in describe(report)))
    return 0 if report.is_clean else DISCREPANCIES_FOUND


def describe(report: ReconciliationReport) -> list[str]:
    findings = report.findings
    lines = [
        f"reconciled {_counted(findings.accounts_checked, 'account', 'accounts')} and"
        f" {_counted(findings.entries_checked, 'entry', 'entries')}"
        f" as of {report.checked_at.isoformat()}"
    ]
    lines += [
        f"balance mismatch: account {mismatch.account_id} (client {mismatch.client_id}) stores"
        f" {format_minor_units(mismatch.stored, mismatch.currency)} but its postings sum to"
        f" {format_minor_units(mismatch.from_postings, mismatch.currency)}"
        for mismatch in findings.balance_mismatches
    ]
    lines += [
        f"unbalanced entry: {imbalance.entry_id} is off by"
        f" {format_minor_units(imbalance.off_by, imbalance.currency)}"
        for imbalance in findings.entry_imbalances
    ]
    lines += [
        f"misdirected posting: entry {posting.entry_id} posts to account {posting.account_id},"
        f" but {posting.problem}"
        for posting in findings.misdirected_postings
    ]
    if report.is_clean:
        lines.append("ok: every balance matches its postings and every entry balances")
    else:
        lines.append(f"found {_counted(report.discrepancies, 'discrepancy', 'discrepancies')}")
    if report.truncated:
        lines.append(f"only the first {report.limit} of each kind are shown; there may be more")
    return lines


def _counted(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number
