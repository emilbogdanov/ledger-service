from __future__ import annotations

import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from ledger.application.errors import InvalidApiKeyError
from ledger.domain.errors import InvalidValueError
from ledger.domain.ids import ClientId

if TYPE_CHECKING:
    # The ports name Client, so importing them at run time here would be circular.
    from ledger.application.ports import UnitOfWork

CLIENT_NAME_MAX_LENGTH: Final = 100
# Tells secret scanners and people reading a config file what the string is.
API_KEY_PREFIX: Final = "lk_"


@dataclass(frozen=True, slots=True)
class Client:
    id: ClientId
    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvalidValueError("client name must not be blank")
        if len(self.name) > CLIENT_NAME_MAX_LENGTH:
            raise InvalidValueError(
                f"client name must be at most {CLIENT_NAME_MAX_LENGTH} characters"
            )


@dataclass(frozen=True, slots=True)
class RegisteredClient:
    client: Client
    # Shown once at registration. Only its hash is stored, so a lost key is replaced, not
    # recovered.
    api_key: str


# A key carries 256 random bits, so unlike a password there is nothing to guess and a fast
# hash is enough: a slow one would only add latency to every request.
def hash_api_key(api_key: str) -> str:
    return sha256(api_key.encode()).hexdigest()


class ClientService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def register(self, name: str) -> RegisteredClient:
        client = Client(ClientId(uuid4()), name)
        api_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        async with self._uow as uow:
            await uow.clients.add(client, api_key_hash=hash_api_key(api_key))
            await uow.commit()
        return RegisteredClient(client, api_key)

    async def authenticate(self, api_key: str) -> Client:
        async with self._uow as uow:
            client = await uow.clients.find_by_api_key_hash(hash_api_key(api_key))
        if client is None:
            raise InvalidApiKeyError
        return client
