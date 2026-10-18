from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from ledger.application.errors import (
    EntryNotFoundError,
    IdempotencyKeyConflictError,
    IdempotencyKeyReusedError,
)
from ledger.application.idempotency import IdempotencyKey, IdempotencyRecord, fingerprint
from ledger.application.ports import Clock, UnitOfWork
from ledger.domain.errors import InvalidValueError
from ledger.domain.ids import AccountId, ClientId, EntryId
from ledger.domain.journal import JournalEntry, Posting
from ledger.domain.money import Money


@dataclass(frozen=True, slots=True)
class PostedEntry:
    entry: JournalEntry
    # True when the key had been used before and this is the outcome of that first request.
    replayed: bool


class PostingService:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def transfer(
        self,
        client_id: ClientId,
        *,
        source: AccountId,
        destination: AccountId,
        amount: Money,
        memo: str,
        idempotency_key: IdempotencyKey,
    ) -> PostedEntry:
        if amount.amount <= 0:
            raise InvalidValueError("transfer amount must be positive")
        return await self.post_entry(
            client_id,
            postings=[Posting(source, -amount), Posting(destination, amount)],
            memo=memo,
            idempotency_key=idempotency_key,
        )

    async def post_entry(
        self,
        client_id: ClientId,
        *,
        postings: Sequence[Posting],
        memo: str,
        idempotency_key: IdempotencyKey,
    ) -> PostedEntry:
        try:
            return await self._post(client_id, postings, memo, idempotency_key)
        except IdempotencyKeyConflictError:
            # A request with the same key committed while this one ran, and this transaction
            # is gone. A second attempt finds that request's record and answers as a retry.
            return await self._post(client_id, postings, memo, idempotency_key)

    async def get_entry(self, client_id: ClientId, entry_id: EntryId) -> JournalEntry:
        async with self._uow as uow:
            entry = await uow.entries.get(client_id, entry_id)
        if entry is None:
            raise EntryNotFoundError(entry_id)
        return entry

    async def _post(
        self,
        client_id: ClientId,
        postings: Sequence[Posting],
        memo: str,
        key: IdempotencyKey,
    ) -> PostedEntry:
        entry = JournalEntry(
            id=EntryId(uuid4()),
            client_id=client_id,
            postings=tuple(postings),
            memo=memo,
            posted_at=self._clock.now(),
        )
        request = fingerprint(entry.postings, memo)
        async with self._uow as uow:
            accounts = await uow.accounts.lock(
                client_id, [posting.account_id for posting in entry.postings]
            )
            # Looked up only once the accounts are locked: a retry of the same request locks
            # the same rows, so it waits for the first attempt to commit and then sees its key.
            earlier = await uow.idempotency.find(client_id, key)
            if earlier is not None:
                return await self._replay(uow, earlier, request)

            entry.apply_to(accounts)
            for account in accounts.values():
                await uow.accounts.update(account)
            await uow.entries.add(entry)
            await uow.idempotency.add(IdempotencyRecord(client_id, key, request, entry.id))
            await uow.commit()
        return PostedEntry(entry, replayed=False)

    @staticmethod
    async def _replay(uow: UnitOfWork, earlier: IdempotencyRecord, request: str) -> PostedEntry:
        if earlier.fingerprint != request:
            raise IdempotencyKeyReusedError(earlier.key)
        entry = await uow.entries.get(earlier.client_id, earlier.entry_id)
        # The record and its entry are written in the same transaction.
        if entry is None:
            raise LookupError(f"idempotency record points at missing entry {earlier.entry_id}")
        return PostedEntry(entry, replayed=True)
