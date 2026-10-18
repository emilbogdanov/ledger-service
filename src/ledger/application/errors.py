from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ledger.application.idempotency import IdempotencyKey
    from ledger.domain.ids import EntryId


class ApplicationError(Exception):
    pass


class EntryNotFoundError(ApplicationError):
    # Also raised for another client's entry, so entry ids can't be probed.
    def __init__(self, entry_id: EntryId) -> None:
        super().__init__(f"entry {entry_id} does not exist")
        self.entry_id = entry_id


class IdempotencyKeyReusedError(ApplicationError):
    def __init__(self, key: IdempotencyKey) -> None:
        super().__init__(f"idempotency key {str(key)!r} was already used for a different request")
        self.key = key


class IdempotencyKeyConflictError(ApplicationError):
    # Raised by the unit of work when another request stored the same key first.
    def __init__(self, key: IdempotencyKey) -> None:
        super().__init__(f"idempotency key {str(key)!r} is already taken")
        self.key = key
