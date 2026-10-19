from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ledger.api.container import Container
from ledger.api.metrics import ApiMetrics
from ledger.application.clients import Client
from ledger.application.errors import InvalidApiKeyError

_bearer = HTTPBearer(auto_error=False, description="The API key from `ledger create-client`")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_metrics(request: Request) -> ApiMetrics:
    metrics: ApiMetrics = request.app.state.metrics
    return metrics


MetricsDep = Annotated[ApiMetrics, Depends(get_metrics)]


async def get_current_client(
    request: Request,
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Client:
    # auto_error is off so a missing key goes through the same 401 path as a wrong one.
    if credentials is None:
        raise InvalidApiKeyError
    client = await container.clients().authenticate(credentials.credentials)
    # For the request log line, which is written outside the dependency's scope.
    request.state.client_id = str(client.id)
    return client


CurrentClient = Annotated[Client, Depends(get_current_client)]
