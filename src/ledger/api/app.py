from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI
from prometheus_client import CollectorRegistry
from starlette.types import Lifespan

from ledger.api.container import Container
from ledger.api.errors import register_error_handlers
from ledger.api.metrics import ApiMetrics
from ledger.api.observability import observe_requests
from ledger.api.routes import accounts, entries


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await container.close()

    app = _routed_app(lifespan)
    app.state.container = container
    observe_requests(app, ApiMetrics(CollectorRegistry()))
    return app


def openapi_schema() -> dict[str, Any]:
    # Routes resolve the container per request, so the schema can be built without a
    # database or any other setting.
    return _routed_app().openapi()


def _routed_app(lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    app = FastAPI(title="Ledger", version=version("ledger"), lifespan=lifespan)
    register_error_handlers(app)
    for router in (accounts.router, entries.router):
        app.include_router(router)

    # Liveness only: answers as long as the process serves requests. It deliberately doesn't
    # touch the database, so a database outage doesn't get every API container restarted.
    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
