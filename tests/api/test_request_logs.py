import logging
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from ledger.api.app import create_app
from ledger.api.container import Container
from tests.api.conftest import NOW, Headers
from tests.unit.application.fakes import FakeUnitOfWork, FixedClock


def request_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "ledger.requests"]


async def test_each_request_is_logged_once_with_its_route_and_client(
    http: AsyncClient, as_acme: Headers, caplog: pytest.LogCaptureFixture
) -> None:
    account_id = uuid4()

    with caplog.at_level(logging.INFO, logger="ledger.requests"):
        response = await http.get(f"/accounts/{account_id}", headers=as_acme)

    (line,) = request_lines(caplog)
    assert line.getMessage() == f"GET /accounts/{account_id} 404"
    assert line.__dict__["route"] == "/accounts/{account_id}"
    assert line.__dict__["status"] == 404
    assert line.__dict__["duration_ms"] >= 0
    assert line.__dict__["client_id"] is not None
    assert response.headers["X-Request-ID"]


async def test_unauthenticated_requests_have_no_client(
    http: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="ledger.requests"):
        await http.get(f"/accounts/{uuid4()}")

    (line,) = request_lines(caplog)
    assert line.__dict__["status"] == 401
    assert line.__dict__["client_id"] is None


async def test_callers_request_id_is_kept(http: AsyncClient) -> None:
    response = await http.get("/nowhere", headers={"X-Request-ID": "checkout-7f3a.2"})

    assert response.headers["X-Request-ID"] == "checkout-7f3a.2"


@pytest.mark.parametrize("unusable", ["", "has spaces", "x" * 129, "line\\nbreak"])
async def test_unusable_request_ids_are_replaced(http: AsyncClient, unusable: str) -> None:
    response = await http.get("/nowhere", headers={"X-Request-ID": unusable})

    assert response.headers["X-Request-ID"] != unusable
    assert len(response.headers["X-Request-ID"]) == 32


async def test_unknown_paths_share_one_route_name(
    http: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="ledger.requests"):
        await http.get("/wp-admin/install.php")

    (line,) = request_lines(caplog)
    assert line.__dict__["route"] == "unmatched"


async def test_health_checks_are_not_logged(
    http: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="ledger.requests"):
        await http.get("/health")

    assert request_lines(caplog) == []


class DatabaseGoneError(Exception):
    pass


def broken_unit_of_work() -> FakeUnitOfWork:
    raise DatabaseGoneError("connection pool exhausted")


async def test_crash_is_logged_with_its_request_id_and_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(Container(uow_factory=broken_unit_of_work, clock=FixedClock(NOW)))
    # Lets the 500 reach the client instead of re-raising the crash inside the test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://ledger.test") as client:
        with caplog.at_level(logging.INFO, logger="ledger.requests"):
            response = await client.get(
                f"/accounts/{uuid4()}", headers={"Authorization": "Bearer lk_anything"}
            )

    assert response.status_code == 500
    (line,) = request_lines(caplog)
    assert line.levelno == logging.ERROR
    assert line.__dict__["status"] == 500
    assert line.exc_info is not None
    assert line.exc_info[0] is DatabaseGoneError
