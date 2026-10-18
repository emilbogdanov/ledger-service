import json

import pytest

from ledger.api.app import openapi_schema
from ledger.cli import main


def test_openapi_prints_the_schema_without_any_settings(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("LEDGER_DATABASE_URL", "LEDGER_REDIS_URL"):
        monkeypatch.delenv(variable, raising=False)

    assert main(["openapi"]) == 0

    printed = capsys.readouterr().out
    assert json.loads(printed) == openapi_schema()
    assert printed.isascii()
