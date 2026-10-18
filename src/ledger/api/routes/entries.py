from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status

from ledger.api.dependencies import ContainerDep, CurrentClient
from ledger.api.errors import error_responses
from ledger.api.schemas import EntryResponse, PostEntry, Transfer
from ledger.application.idempotency import IdempotencyKey
from ledger.application.postings import PostedEntry
from ledger.domain.ids import AccountId, EntryId
from ledger.domain.journal import Posting
from ledger.domain.money import Money

router = APIRouter(tags=["entries"], responses=error_responses(401, 404, 422))

IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        description=(
            "Unique per request, chosen by the caller. Sending the same key with the same body "
            "again returns the first result instead of posting twice."
        ),
    ),
]

_REPLAYED = {
    status.HTTP_200_OK: {
        "model": EntryResponse,
        "description": "The key was used before: this is the entry that request posted.",
    },
    **error_responses(409),
}


@router.post("/transfers", status_code=status.HTTP_201_CREATED, responses=_REPLAYED)
async def transfer(
    body: Transfer,
    idempotency_key: IdempotencyKeyHeader,
    client: CurrentClient,
    container: ContainerDep,
    response: Response,
) -> EntryResponse:
    posted = await container.postings().transfer(
        client.id,
        source=AccountId(body.source_account_id),
        destination=AccountId(body.destination_account_id),
        amount=Money(body.amount, body.currency),
        memo=body.memo,
        idempotency_key=IdempotencyKey(idempotency_key),
    )
    return _respond(posted, response)


@router.post("/entries", status_code=status.HTTP_201_CREATED, responses=_REPLAYED)
async def post_entry(
    body: PostEntry,
    idempotency_key: IdempotencyKeyHeader,
    client: CurrentClient,
    container: ContainerDep,
    response: Response,
) -> EntryResponse:
    posted = await container.postings().post_entry(
        client.id,
        postings=[
            Posting(AccountId(posting.account_id), Money(posting.amount, posting.currency))
            for posting in body.postings
        ],
        memo=body.memo,
        idempotency_key=IdempotencyKey(idempotency_key),
    )
    return _respond(posted, response)


@router.get("/entries/{entry_id}")
async def get_entry(
    entry_id: UUID, client: CurrentClient, container: ContainerDep
) -> EntryResponse:
    return EntryResponse.of(await container.postings().get_entry(client.id, EntryId(entry_id)))


def _respond(posted: PostedEntry, response: Response) -> EntryResponse:
    response.headers["Location"] = f"/entries/{posted.entry.id}"
    if posted.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return EntryResponse.of(posted.entry)
