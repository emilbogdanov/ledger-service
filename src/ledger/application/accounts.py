from uuid import uuid4

from ledger.application.events import account_opened
from ledger.application.ports import Clock, UnitOfWork
from ledger.domain.accounts import Account, AccountName, OverdraftPolicy
from ledger.domain.errors import AccountNotFoundError
from ledger.domain.ids import AccountId, ClientId
from ledger.domain.money import Currency


class AccountService:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def open_account(
        self,
        client_id: ClientId,
        *,
        name: AccountName,
        currency: Currency,
        overdraft: OverdraftPolicy,
    ) -> Account:
        account = Account.open(
            account_id=AccountId(uuid4()),
            client_id=client_id,
            name=name,
            currency=currency,
            overdraft=overdraft,
        )
        async with self._uow as uow:
            await uow.accounts.add(account)
            await uow.outbox.add(account_opened(account, at=self._clock.now()))
            await uow.commit()
        return account

    async def get_account(self, client_id: ClientId, account_id: AccountId) -> Account:
        async with self._uow as uow:
            account = await uow.accounts.get(client_id, account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return account

    async def change_overdraft(
        self, client_id: ClientId, account_id: AccountId, overdraft: OverdraftPolicy
    ) -> Account:
        async with self._uow as uow:
            # Locked like a posting would lock it: saving an account read without the lock
            # could write back a balance that a concurrent transfer has already moved.
            account = (await uow.accounts.lock(client_id, [account_id])).get(account_id)
            if account is None:
                raise AccountNotFoundError(account_id)
            account.change_overdraft(overdraft)
            await uow.accounts.update(account)
            await uow.commit()
        return account
