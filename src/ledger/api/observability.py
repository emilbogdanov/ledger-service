import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Final
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ledger.api.metrics import ApiMetrics
from ledger.logs import request_id

logger = logging.getLogger("ledger.requests")

REQUEST_ID_HEADER: Final = "X-Request-ID"
# A caller's id is kept so its logs and ours line up, but only if it is short and plain: it
# ends up in every log line of the request.
_ACCEPTABLE_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9._-]{1,128}")
# Polled by load balancers and Prometheus; a line per poll would bury the real traffic.
_UNOBSERVED_PATHS: Final = frozenset({"/health", "/metrics"})

type CallNext = Callable[[Request], Awaitable[Response]]


def observe_requests(app: FastAPI, metrics: ApiMetrics) -> None:
    app.state.metrics = metrics

    # Unauthenticated, like most scrape targets: keep it off the public network rather than
    # behind an API key Prometheus would need to hold.
    @app.get("/metrics", include_in_schema=False)
    async def expose_metrics() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.middleware("http")
    async def log_request(request: Request, call_next: CallNext) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        current = incoming if _ACCEPTABLE_REQUEST_ID.fullmatch(incoming) else uuid4().hex
        token = request_id.set(current)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Answered with a 500 further out; logged here, where the request id is still set.
            _observe(metrics, request, 500, started, level=logging.ERROR, exc_info=True)
            raise
        else:
            response.headers[REQUEST_ID_HEADER] = current
            _observe(metrics, request, response.status_code, started)
            return response
        finally:
            request_id.reset(token)


def route_of(request: Request) -> str:
    # The route template, not the raw path: /accounts/{account_id} rather than one entry per
    # account, which keeps logs groupable and metric label counts bounded. The router records
    # the matched route in the scope; a path no route matched has none.
    route = request.scope.get("route")
    return str(getattr(route, "path", "unmatched"))


def _observe(
    metrics: ApiMetrics,
    request: Request,
    status: int,
    started: float,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
) -> None:
    if request.url.path in _UNOBSERVED_PATHS:
        return
    seconds = time.perf_counter() - started
    route = route_of(request)
    metrics.requests.labels(request.method, route, str(status)).inc()
    metrics.latency.labels(request.method, route).observe(seconds)
    logger.log(
        level,
        "%s %s %d",
        request.method,
        request.url.path,
        status,
        exc_info=exc_info,
        extra={
            "method": request.method,
            "route": route,
            "status": status,
            "duration_ms": round(seconds * 1000, 2),
            "client_id": getattr(request.state, "client_id", None),
        },
    )
