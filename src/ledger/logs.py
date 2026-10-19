import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Final, Literal

# Set for the length of one request, so every line logged while serving it can be tied back to
# it, including lines from code that knows nothing about HTTP.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Everything a LogRecord carries by itself. Anything else on a record came from `extra=` and is
# written out as a field of its own.
_RECORD_ATTRIBUTES: Final = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if (current := request_id.get()) is not None:
            line["request_id"] = current
        line.update(
            (key, value) for key, value in vars(record).items() if key not in _RECORD_ATTRIBUTES
        )
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        # default=str keeps a stray UUID or datetime in `extra=` from losing the whole line.
        return json.dumps(line, default=str, ensure_ascii=False)


def configure_logging(level: str, log_format: Literal["json", "text"]) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if log_format == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
