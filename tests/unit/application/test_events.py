import json
from datetime import UTC, datetime
from uuid import UUID

from ledger.application.events import account_opened, entry_posted
from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Currency, Money

CLIENT = ClientId(UUID("0b6f2c8e-3a41-4d6e-9c55-7e1d2a9f4b10"))
WALLET = AccountId(UUID("6a1e9c37-4d2b-4f8a-b5e0-93c7d1f2a846"))
MERCHANT = AccountId(UUID("d4f8b2a1-7e3c-4b69-8a05-1c2e9f6d3b77"))
AT = datetime(2026, 10, 18, 14, 5, tzinfo=UTC)


def test_entry_posted_describes_every_posting() -> None:
    entry = JournalEntry(
        id=EntryId(UUID("e2b7c4d9-1f6a-4385-9c0e-7a3d5b8f2c16")),
        client_id=CLIENT,
        postings=(
            Posting(WALLET, Money(-2_499, Currency.EUR)),
            Posting(MERCHANT, Money(2_499, Currency.EUR)),
        ),
        memo="Order 10432",
        posted_at=AT,
    )

    event = entry_posted(entry)

    assert event.type == "entry.posted"
    assert event.occurred_at == AT
    assert event.payload == {
        "entry_id": "e2b7c4d9-1f6a-4385-9c0e-7a3d5b8f2c16",
        "client_id": str(CLIENT),
        "memo": "Order 10432",
        "postings": [
            {"account_id": str(WALLET), "amount": -2_499, "currency": "EUR"},
            {"account_id": str(MERCHANT), "amount": 2_499, "currency": "EUR"},
        ],
    }


def test_account_opened_carries_the_overdraft_limit() -> None:
    account = Account.open(
        account_id=WALLET,
        client_id=CLIENT,
        name=AccountName("Customer wallet"),
        currency=Currency.JPY,
        overdraft=OverdraftPolicy.unlimited(),
    )

    event = account_opened(account, at=AT)

    assert event.type == "account.opened"
    assert event.payload == {
        "account_id": str(WALLET),
        "client_id": str(CLIENT),
        "name": "Customer wallet",
        "currency": "JPY",
        "overdraft_limit": None,
    }


def test_payloads_are_plain_json() -> None:
    account = Account.open(
        account_id=WALLET,
        client_id=CLIENT,
        name=AccountName("Customer wallet"),
        currency=Currency.EUR,
        overdraft=OverdraftPolicy.up_to(5_000),
    )
    payload = dict(account_opened(account, at=AT).payload)

    assert json.loads(json.dumps(payload)) == payload
