from uuid import UUID

from fastapi import APIRouter, Response, status

from ledger.api.dependencies import ContainerDep, CurrentClient
from ledger.api.errors import error_responses
from ledger.api.schemas import AccountResponse, ChangeOverdraft, OpenAccount
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
