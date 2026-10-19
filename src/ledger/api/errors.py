from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ledger.api.schemas import ErrorResponse
from ledger.application.errors import (
    EntryNotFoundError,
    IdempotencyKeyConflictError,
    IdempotencyKeyReusedError,
    InvalidApiKeyError,
)
from ledger.domain.errors import (
    AccountNotFoundError,
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidEntryError,
    InvalidValueError,
    UnbalancedEntryError,
)

if TYPE_CHECKING:
    from ledger.api.metrics import ApiMetrics

# The one place where business errors become HTTP responses. The code is stable and meant for
# clients to branch on; the message is for people.
ERROR_RESPONSES: dict[type[Exception], tuple[int, str]] = {
    InvalidValueError: (422, "invalid_value"),
    CurrencyMismatchError: (422, "currency_mismatch"),
    InvalidEntryError: (422, "invalid_entry"),
    UnbalancedEntryError: (422, "unbalanced_entry"),
    InsufficientFundsError: (422, "insufficient_funds"),
    IdempotencyKeyReusedError: (422, "idempotency_key_reused"),
    InvalidApiKeyError: (401, "invalid_api_key"),
    AccountNotFoundError: (404, "account_not_found"),
    EntryNotFoundError: (404, "entry_not_found"),
    # Only when the retry after a lost race on the key collides again, which takes a third
    # request using the same key at the same moment.
    IdempotencyKeyConflictError: (409, "idempotency_key_conflict"),
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries so generated clients know every error has the ErrorResponse shape."""
    return {status_code: {"model": ErrorResponse} for status_code in status_codes}


def register_error_handlers(app: FastAPI) -> None:
    for error_type in ERROR_RESPONSES:
        app.add_exception_handler(error_type, _to_response)
    app.exception_handler(RequestValidationError)(_malformed_request)
    # Everything else is a bug, including a business error nobody mapped. Starlette answers it
    # with this handler and then re-raises it, so the server logs the full traceback.
    app.add_exception_handler(Exception, _unexpected)


# FastAPI's default 422 body has a different shape from every other error. Clients get one
# shape to handle instead, with the per-field details kept under "errors".
async def _malformed_request(request: Request, error: RequestValidationError) -> JSONResponse:
    _count(request, "invalid_request")
    return JSONResponse(
        {
            "detail": "request is malformed",
            "code": "invalid_request",
            "errors": jsonable_encoder(error.errors()),
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


async def _to_response(request: Request, error: Exception) -> JSONResponse:
    # Handlers are registered per mapped type, so one of the error's classes is in the table,
    # and walking the MRO picks the most specific mapping.
    status_code, code = next(
        ERROR_RESPONSES[cls] for cls in type(error).__mro__ if cls in ERROR_RESPONSES
    )
    _count(request, code)
    return JSONResponse(
        {"detail": str(error), "code": code},
        status_code=status_code,
        headers={"WWW-Authenticate": "Bearer"}
        if status_code == status.HTTP_401_UNAUTHORIZED
        else None,
    )


async def _unexpected(request: Request, _: Exception) -> JSONResponse:
    _count(request, "internal")
    return JSONResponse(
        {"detail": "internal error", "code": "internal"},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _count(request: Request, code: str) -> None:
    metrics: ApiMetrics = request.app.state.metrics
    metrics.errors.labels(code).inc()
