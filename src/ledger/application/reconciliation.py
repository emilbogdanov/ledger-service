from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    # The ports name the types below, so importing them at run time here would be circular.
    from ledger.application.ports import Clock, LedgerAudit
    from ledger.domain.ids import AccountId, ClientId, EntryId
    from ledger.domain.money import Currency

# Findings hold plain integers rather than Money: a corrupted ledger can produce sums outside
# the range Money accepts, and the report must still be able to show them.


@dataclass(frozen=True, slots=True)
class BalanceMismatch:
    account_id: AccountId
    client_id: ClientId
    currency: Currency
    stored: int
    from_postings: int


@dataclass(frozen=True, slots=True)
class EntryImbalance:
    entry_id: EntryId
    currency: Currency
    off_by: int


class PostingProblem(StrEnum):
    WRONG_CURRENCY = "its currency differs from the account's"
    WRONG_CLIENT = "the account belongs to another client"


@dataclass(frozen=True, slots=True)
class MisdirectedPosting:
    entry_id: EntryId
    account_id: AccountId
    problem: PostingProblem


@dataclass(frozen=True, slots=True)
class AuditFindings:
    accounts_checked: int
    entries_checked: int
    balance_mismatches: list[BalanceMismatch]
    entry_imbalances: list[EntryImbalance]
    misdirected_postings: list[MisdirectedPosting]


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    checked_at: datetime
    findings: AuditFindings
    # Each kind of finding is capped at this many, so a badly broken ledger still gives a
    # readable report. Hitting the cap means there may be more.
    limit: int

    @property
    def discrepancies(self) -> int:
        return (
            len(self.findings.balance_mismatches)
            + len(self.findings.entry_imbalances)
            + len(self.findings.misdirected_postings)
        )

    @property
    def is_clean(self) -> bool:
        return self.discrepancies == 0

    @property
    def truncated(self) -> bool:
        return self.limit in {
            len(self.findings.balance_mismatches),
            len(self.findings.entry_imbalances),
            len(self.findings.misdirected_postings),
        }


class ReconciliationService:
    def __init__(self, audit: LedgerAudit, clock: Clock) -> None:
        self._audit = audit
        self._clock = clock

    async def reconcile(self, *, limit: int) -> ReconciliationReport:
        checked_at = self._clock.now()
        return ReconciliationReport(checked_at, await self._audit.examine(limit=limit), limit)
