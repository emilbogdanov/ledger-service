import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from uuid import UUID

from ledger.application.history import HistoryCursor
from ledger.domain.errors import InvalidValueError
from ledger.domain.ids import EntryId

# Clients treat cursors as opaque strings. They aren't signed: editing one only moves where a
# page starts inside a history the caller may read anyway, so there is nothing to protect.


def encode_cursor(cursor: HistoryCursor) -> str:
    payload = {"at": cursor.posted_at.isoformat(), "entry": str(cursor.entry_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str) -> HistoryCursor:
    try:
        payload = json.loads(urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        posted_at = datetime.fromisoformat(payload["at"])
        entry_id = EntryId(UUID(payload["entry"]))
    except (ValueError, KeyError, TypeError) as error:
        raise InvalidValueError("cursor is invalid") from error
    # Compared against timestamptz values, a naive time would silently mean UTC.
    if posted_at.tzinfo is None:
        raise InvalidValueError("cursor is invalid")
    return HistoryCursor(posted_at, entry_id)
