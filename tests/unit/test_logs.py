import json
import logging
import sys
from uuid import UUID

import pytest

from ledger.logs import JsonFormatter, configure_logging, request_id


def record(message: str = "relayed %d events", *args: object, **extra: object) -> logging.LogRecord:
    made = logging.makeLogRecord(
        {"name": "ledger.worker", "levelno": logging.INFO, "levelname": "INFO", "msg": message}
    )
    made.args = args or (3,)
    made.created = 1_792_389_600.25
    for key, value in extra.items():
        setattr(made, key, value)
    return made


def test_lines_are_json_with_extra_fields_flattened() -> None:
    line = json.loads(JsonFormatter().format(record(stream="ledger:events", batch=3)))

    assert line == {
        "time": "2026-10-19T06:00:00.250+00:00",
        "level": "info",
        "logger": "ledger.worker",
        "message": "relayed 3 events",
        "stream": "ledger:events",
        "batch": 3,
    }


def test_request_id_in_context_is_added_to_every_line() -> None:
    token = request_id.set("3f2a9c")
    try:
        line = json.loads(JsonFormatter().format(record()))
    finally:
        request_id.reset(token)

    assert line["request_id"] == "3f2a9c"
    assert "request_id" not in json.loads(JsonFormatter().format(record()))


def test_values_json_cannot_encode_are_written_as_text() -> None:
    entry = UUID("e2b7c4d9-1f6a-4385-9c0e-7a3d5b8f2c16")

    line = json.loads(JsonFormatter().format(record(entry_id=entry)))

    assert line["entry_id"] == str(entry)


def parse_payload() -> None:
    raise ValueError("payload is not JSON")


def test_exceptions_are_kept_in_one_line() -> None:
    failed = record()
    try:
        parse_payload()
    except ValueError:
        failed.exc_info = sys.exc_info()

    formatted = JsonFormatter().format(failed)

    assert "\n" not in formatted
    assert "ValueError: payload is not JSON" in json.loads(formatted)["exception"]


@pytest.mark.parametrize(("log_format", "starts_with"), [("json", "{"), ("text", "20")])
def test_configured_format_reaches_stdout(
    capsys: pytest.CaptureFixture[str], log_format: str, starts_with: str
) -> None:
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging("INFO", "json" if log_format == "json" else "text")
        logging.getLogger("ledger.test").info("configured")
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)

    out = capsys.readouterr().out
    assert out.startswith(starts_with)
    assert "configured" in out
