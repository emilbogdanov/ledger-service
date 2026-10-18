import json
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Final

from ledger.domain.errors import InvalidValueError
from ledger.domain.ids import ClientId, EntryId
from ledger.domain.journal import Posting

IDEMPOTENCY_KEY_MAX_LENGTH: Final = 255


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise InvalidValueError("idempotency key must not be blank")
        if len(self.value) > IDEMPOTENCY_KEY_MAX_LENGTH:
            raise InvalidValueError(
                f"idempotency key must be at most {IDEMPOTENCY_KEY_MAX_LENGTH} characters"
            )
        # Keys arrive in an HTTP header, which only carries printable ASCII.
        if not (self.value.isascii() and self.value.isprintable()):
            raise InvalidValueError("idempotency key must be printable ASCII")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    client_id: ClientId
    key: IdempotencyKey
    fingerprint: str
    entry_id: EntryId


# Postings are sorted first: the same transfer with its legs listed the other way round is
# the same request, not a reuse of the key.
def fingerprint(postings: Iterable[Posting], memo: str) -> str:
    legs = sorted(
        (str(posting.account_id), posting.amount.amount, str(posting.amount.currency))
        for posting in postings
    )
    canonical = json.dumps({"memo": memo, "postings": legs}, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()
