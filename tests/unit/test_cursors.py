from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ledger.api.cursors import decode_cursor, encode_cursor
from ledger.application.history import HistoryCursor
from ledger.domain.errors import InvalidValueError
from ledger.domain.ids import EntryId


def test_cursor_round_trips() -> None:
    cursor = HistoryCursor(datetime(2026, 10, 18, 9, 41, 3, 250_000, tzinfo=UTC), EntryId(uuid4()))

    token = encode_cursor(cursor)

    assert decode_cursor(token) == cursor
    assert "=" not in token


def raw_token(payload: bytes) -> str:
    return urlsafe_b64encode(payload).decode()


@pytest.mark.parametrize(
    "token",
    [
        "not base64 at all!",
        raw_token(b"[1, 2]"),
        raw_token(b'{"at": "2026-10-18T09:41:03+00:00"}'),
        raw_token(b'{"at": "yesterday", "entry": "8c1f2a4e-7d55-4b0e-9f3a-2c6d8e1b4a07"}'),
        raw_token(
            b'{"at": "2026-10-18T09:41:03", "entry": "8c1f2a4e-7d55-4b0e-9f3a-2c6d8e1b4a07"}'
        ),
    ],
)
def test_malformed_cursors_are_refused(token: str) -> None:
    with pytest.raises(InvalidValueError, match="cursor is invalid"):
        decode_cursor(token)
