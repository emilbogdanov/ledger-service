import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from ledger.api.app import openapi_schema
from ledger.application.reconciliation import (
    AuditFindings,
    BalanceMismatch,
    EntryImbalance,
    MisdirectedPosting,
    PostingProblem,
    ReconciliationReport,
)
from ledger.cli import DISCREPANCIES_FOUND, main
from ledger.config import Settings
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.money import Currency


def test_openapi_prints_the_schema_without_any_settings(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("LEDGER_DATABASE_URL", "LEDGER_REDIS_URL"):
        monkeypatch.delenv(variable, raising=False)

    assert main(["openapi"]) == 0

    printed = capsys.readouterr().out
    assert json.loads(printed) == openapi_schema()
    assert printed.isascii()


CHECKED_AT = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
DATABASE_URL = "postgresql+asyncpg://ledger@db/ledger"
ACCOUNT = AccountId(UUID("6a1e9c37-4d2b-4f8a-b5e0-93c7d1f2a846"))
CLIENT = ClientId(UUID("0b6f2c8e-3a41-4d6e-9c55-7e1d2a9f4b10"))
ENTRY = EntryId(UUID("e2b7c4d9-1f6a-4385-9c0e-7a3d5b8f2c16"))


def report(*, limit: int = 100, **findings: list[Any]) -> ReconciliationReport:
    return ReconciliationReport(
        CHECKED_AT,
        AuditFindings(
            accounts_checked=12,
            entries_checked=340,
            balance_mismatches=findings.get("balance_mismatches", []),
            entry_imbalances=findings.get("entry_imbalances", []),
            misdirected_postings=findings.get("misdirected_postings", []),
        ),
        limit,
    )


@pytest.fixture
def reconciled(monkeypatch: pytest.MonkeyPatch) -> Callable[[ReconciliationReport], list[int]]:
    limits: list[int] = []

    def returning(result: ReconciliationReport) -> list[int]:
        async def fake_reconcile(settings: Settings, *, limit: int) -> ReconciliationReport:
            assert str(settings.database_url) == DATABASE_URL
            limits.append(limit)
            return result

        monkeypatch.setenv("LEDGER_DATABASE_URL", DATABASE_URL)
        monkeypatch.setenv("LEDGER_REDIS_URL", "redis://cache:6379/0")
        monkeypatch.setattr("ledger.cli.reconcile", fake_reconcile)
        return limits

    return returning


def test_clean_ledger_exits_zero(
    capsys: pytest.CaptureFixture[str],
    reconciled: Callable[[ReconciliationReport], list[int]],
) -> None:
    limits = reconciled(report())

    assert main(["reconcile"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "reconciled 12 accounts and 340 entries as of 2026-10-19T06:00:00+00:00",
        "ok: every balance matches its postings and every entry balances",
    ]
    assert limits == [100]


def test_discrepancies_are_listed_and_exit_two(
    capsys: pytest.CaptureFixture[str],
    reconciled: Callable[[ReconciliationReport], list[int]],
) -> None:
    reconciled(
        report(
            balance_mismatches=[BalanceMismatch(ACCOUNT, CLIENT, Currency.EUR, 10_005, 10_000)],
            entry_imbalances=[EntryImbalance(ENTRY, Currency.JPY, -100)],
            misdirected_postings=[MisdirectedPosting(ENTRY, ACCOUNT, PostingProblem.WRONG_CLIENT)],
        )
    )

    assert main(["reconcile"]) == DISCREPANCIES_FOUND

    assert capsys.readouterr().out.splitlines()[1:] == [
        f"balance mismatch: account {ACCOUNT} (client {CLIENT}) stores 100.05 EUR"
        " but its postings sum to 100.00 EUR",
        f"unbalanced entry: {ENTRY} is off by -100 JPY",
        f"misdirected posting: entry {ENTRY} posts to account {ACCOUNT},"
        " but the account belongs to another client",
        "found 3 discrepancies",
    ]


def test_single_counts_read_naturally(
    capsys: pytest.CaptureFixture[str],
    reconciled: Callable[[ReconciliationReport], list[int]],
) -> None:
    single = report(entry_imbalances=[EntryImbalance(ENTRY, Currency.EUR, 5)])
    reconciled(
        ReconciliationReport(
            CHECKED_AT,
            AuditFindings(1, 1, [], single.findings.entry_imbalances, []),
            100,
        )
    )

    main(["reconcile"])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("reconciled 1 account and 1 entry as of")
    assert lines[-1] == "found 1 discrepancy"


def test_amounts_beyond_the_money_range_are_still_shown(
    capsys: pytest.CaptureFixture[str],
    reconciled: Callable[[ReconciliationReport], list[int]],
) -> None:
    beyond = 2**64
    reconciled(
        report(balance_mismatches=[BalanceMismatch(ACCOUNT, CLIENT, Currency.JPY, 0, beyond)])
    )

    assert main(["reconcile"]) == DISCREPANCIES_FOUND
    assert f"sum to {beyond} JPY" in capsys.readouterr().out


def test_truncated_report_says_so(
    capsys: pytest.CaptureFixture[str],
    reconciled: Callable[[ReconciliationReport], list[int]],
) -> None:
    limits = reconciled(report(limit=1, entry_imbalances=[EntryImbalance(ENTRY, Currency.EUR, 5)]))

    assert main(["reconcile", "--limit", "1"]) == DISCREPANCIES_FOUND
    assert capsys.readouterr().out.splitlines()[-1] == (
        "only the first 1 of each kind are shown; there may be more"
    )
    assert limits == [1]


@pytest.mark.parametrize("limit", ["0", "-3", "many"])
def test_limit_must_be_a_positive_number(limit: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["reconcile", "--limit", limit])

    assert excinfo.value.code == 2
