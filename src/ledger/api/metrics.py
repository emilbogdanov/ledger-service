from prometheus_client import CollectorRegistry, Counter, Histogram


# One registry per app instead of prometheus_client's global one, so two apps in one process
# (as in the tests) never share or double-register series.
class ApiMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.requests = Counter(
            "ledger_http_requests",
            "HTTP requests answered, by route template and status code.",
            ["method", "route", "status"],
            registry=registry,
        )
        self.latency = Histogram(
            "ledger_http_request_duration_seconds",
            "Time from receiving a request to answering it.",
            ["method", "route"],
            registry=registry,
        )
        self.entries = Counter(
            "ledger_entries",
            "Entries accepted: posted for the first time, or replayed for a retried key.",
            ["outcome"],
            registry=registry,
        )
        self.errors = Counter(
            "ledger_errors",
            "Requests refused, by the error code clients receive.",
            ["code"],
            registry=registry,
        )
