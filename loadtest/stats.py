import math
from collections import Counter
from dataclasses import dataclass, field


def percentile(samples: list[float], fraction: float) -> float:
    # Nearest-rank: the smallest sample that at least `fraction` of all samples don't exceed,
    # so every reported latency is one a request actually had.
    if not samples:
        raise ValueError("no samples")
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(samples)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


@dataclass
class Tally:
    latencies_ms: list[float] = field(default_factory=list)
    # By HTTP status, with the error code for refusals: "201", "200", "422 insufficient_funds".
    outcomes: Counter[str] = field(default_factory=Counter)

    def record(self, outcome: str, latency_ms: float) -> None:
        self.outcomes[outcome] += 1
        self.latencies_ms.append(latency_ms)

    @property
    def requests(self) -> int:
        return sum(self.outcomes.values())

    @property
    def unexpected(self) -> int:
        # Anything but a posted entry, a replay or a refusal for lack of funds is a failure.
        expected = {"201", "200", "422 insufficient_funds"}
        return sum(count for outcome, count in self.outcomes.items() if outcome not in expected)
