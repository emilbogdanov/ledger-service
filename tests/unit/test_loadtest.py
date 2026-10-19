import argparse

import pytest
from loadtest.run import SCENARIOS, ScenarioResult, parse_args, report
from loadtest.stats import Tally, percentile


@pytest.mark.parametrize(
    ("fraction", "expected"), [(0.5, 50.0), (0.95, 95.0), (0.99, 99.0), (1.0, 100.0)]
)
def test_percentiles_are_nearest_rank(fraction: float, expected: float) -> None:
    samples = [float(n) for n in range(100, 0, -1)]

    assert percentile(samples, fraction) == expected


def test_percentile_of_one_sample_is_that_sample() -> None:
    assert percentile([12.5], 0.99) == 12.5


@pytest.mark.parametrize(("samples", "fraction"), [([], 0.5), ([1.0], 0.0), ([1.0], 1.5)])
def test_percentile_rejects_what_it_cannot_answer(samples: list[float], fraction: float) -> None:
    with pytest.raises(ValueError, match=r"samples|fraction"):
        percentile(samples, fraction)


def test_only_unexpected_responses_count_as_failures() -> None:
    tally = Tally()
    for outcome in ("201", "201", "200", "422 insufficient_funds", "500 internal", "409 conflict"):
        tally.record(outcome, 3.0)

    assert tally.requests == 6
    assert tally.unexpected == 2


def test_report_has_one_row_per_scenario_and_the_checks() -> None:
    tally = Tally()
    for latency in (4.0, 6.0, 8.0, 30.0):
        tally.record("201", latency)
    args = argparse.Namespace(accounts=50, concurrency=32, duration=30.0, retry_share=0.1)

    text = report(
        [ScenarioResult(scenario, tally, seconds=2.0) for scenario in SCENARIOS],
        args=args,
        total_balance=0,
        drain_seconds=0.4,
    )

    assert "| spread | 4 | 2 | 2 | 6.0 | 30.0 | 30.0 |" in text
    assert "| hot account | 4 | 2 | 2 | 6.0 | 30.0 | 30.0 |" in text
    assert "- Sum of every balance the test touched: 0 (must be 0)" in text
    assert "- Outbox drained 0.4 s after the last request" in text


def test_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_LOAD_API_KEY", raising=False)

    with pytest.raises(SystemExit):
        parse_args([])
