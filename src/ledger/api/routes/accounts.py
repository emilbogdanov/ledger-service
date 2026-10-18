from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ledger.api.cursors import decode_cursor, encode_cursor
from ledger.api.dependencies import ContainerDep, CurrentClient
from ledger.api.errors import error_responses
from ledger.api.schemas import (
    AccountPostingResponse,
    AccountResponse,
    ChangeOverdraft,
    HistoryPageResponse,
    OpenAccount,
)
from ledger.application.history import MAX_PAGE_SIZE
from ledger.domain.accounts import AccountName, OverdraftPolicy
from ledger.domain.ids import AccountId

router = APIRouter(prefix="/accounts", tags=["accounts"], responses=error_responses(401, 422))


@router.post("", status_code=status.HTTP_201_CREATED)
async def open_account(
    body: OpenAccount, client: CurrentClient, container: ContainerDep, response: Response
) -> AccountResponse:
    account = await container.accounts().open_account(
        client.id,
        name=AccountName(body.name),
        currency=body.currency,
        overdraft=OverdraftPolicy(body.overdraft_limit),
    )
    response.headers["Location"] = f"/accounts/{account.id}"
    return AccountResponse.of(account)


@router.get("/{account_id}", responses=error_responses(404))
async def get_account(
    account_id: UUID, client: CurrentClient, container: ContainerDep
) -> AccountResponse:
    account = await container.accounts().get_account(client.id, AccountId(account_id))
    return AccountResponse.of(account)


@router.patch("/{account_id}", responses=error_responses(404))
async def change_overdraft(
    account_id: UUID, body: ChangeOverdraft, client: CurrentClient, container: ContainerDep
) -> AccountResponse:
    account = await container.accounts().change_overdraft(
        client.id, AccountId(account_id), OverdraftPolicy(body.overdraft_limit)
    )
    return AccountResponse.of(account)


@router.get("/{account_id}/postings", responses=error_responses(404))
async def account_history(
    account_id: UUID,
    client: CurrentClient,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    cursor: Annotated[str | None, Query(description="next_cursor from the previous page")] = None,
) -> HistoryPageResponse:
    page = await container.history().history(
        client.id,
        AccountId(account_id),
        after=decode_cursor(cursor) if cursor is not None else None,
        limit=limit,
    )
    return HistoryPageResponse(
        postings=[AccountPostingResponse.of(posting) for posting in page.postings],
        next_cursor=encode_cursor(page.next_cursor) if page.next_cursor is not None else None,
    )
