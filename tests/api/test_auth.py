import pytest
from httpx import AsyncClient


# A client is registered, so the refusals below are about the key and not an empty system.
@pytest.mark.usefixtures("as_acme")
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer lk_revoked-or-made-up"},
        {"Authorization": "Basic YWNtZTpzZWNyZXQ="},
    ],
)
async def test_requests_without_a_valid_key_are_refused(
    http: AsyncClient, headers: dict[str, str]
) -> None:
    response = await http.post(
        "/accounts", json={"name": "Wallet", "currency": "EUR"}, headers=headers
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "API key is missing or invalid", "code": "invalid_api_key"}
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_health_needs_no_key(http: AsyncClient) -> None:
    response = await http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
