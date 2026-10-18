from datetime import datetime
from typing import Annotated, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ledger.domain.accounts import Account
from ledger.domain.journal import JournalEntry
from ledger.domain.money import MAX_AMOUNT, Currency

# Bounds how many account rows a single request can hold locked.
MAX_POSTINGS_PER_ENTRY: Final = 50

_Limit = Annotated[int, Field(ge=0, le=MAX_AMOUNT)]
_LIMIT_DESCRIPTION = (
    "How far below zero the balance may go, in minor units of the account's currency. "
    "0 forbids overdraft; null removes the floor entirely."
)


class _Request(BaseModel):
    # A misspelt field is reported instead of silently falling back to its default.
    model_config = ConfigDict(extra="forbid")


class ErrorResponse(BaseModel):
    detail: str
    code: str


class OpenAccount(_Request):
    name: str
    currency: Currency
    overdraft_limit: _Limit | None = Field(default=0, description=_LIMIT_DESCRIPTION)


class ChangeOverdraft(_Request):
    overdraft_limit: _Limit | None = Field(description=_LIMIT_DESCRIPTION)


class AccountResponse(BaseModel):
    id: UUID
    name: str
    currency: Currency
    # In minor units of the currency: 1050 EUR cents is 10.50 EUR.
    balance: int
    overdraft_limit: int | None

    @classmethod
    def of(cls, account: Account) -> Self:
        return cls(
            id=account.id,
            name=str(account.name),
            currency=account.currency,
            balance=account.balance.amount,
            overdraft_limit=account.overdraft.limit,
        )


class Transfer(_Request):
    source_account_id: UUID
    destination_account_id: UUID
    amount: Annotated[int, Field(gt=0, le=MAX_AMOUNT, description="In minor units.")]
    currency: Currency
    memo: str = ""


class PostingRequest(_Request):
    account_id: UUID
    amount: Annotated[
        int,
        Field(
            ge=-MAX_AMOUNT,
            le=MAX_AMOUNT,
            description="In minor units: positive pays into the account, negative takes out.",
        ),
    ]
    currency: Currency


class PostEntry(_Request):
    postings: list[PostingRequest] = Field(max_length=MAX_POSTINGS_PER_ENTRY)
    memo: str = ""


class PostingResponse(BaseModel):
    account_id: UUID
    amount: int
    currency: Currency


class EntryResponse(BaseModel):
    id: UUID
    memo: str
    posted_at: datetime
    postings: list[PostingResponse]

    @classmethod
    def of(cls, entry: JournalEntry) -> Self:
        return cls(
            id=entry.id,
            memo=entry.memo,
            posted_at=entry.posted_at,
            postings=[
                PostingResponse(
                    account_id=posting.account_id,
                    amount=posting.amount.amount,
                    currency=posting.amount.currency,
                )
                for posting in entry.postings
            ],
        )
