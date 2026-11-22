"""A rule-based exposure gate and its bookkeeping. Pure arithmetic: no data access, no fitting.

Index i is a decision made after session i's close. Its target is traded at the open of
session i + 1. Lists are aligned by session.
"""

from collections.abc import Sequence
import random
from statistics import fmean


def gate_targets(signals: Sequence[Sequence[bool | None]], exposure_by_count: Sequence[float]) -> list[tuple[float | None, bool]]:
    """Target exposure per decision, as (target, held).

    Each decision has one entry per rule: True stressed, False not, None unavailable. The
    gate starts at the first decision where every rule is available; before that the target
    is None. From then on the target is ``exposure_by_count`` at the number of stressed
    rules (the last entry serves every higher count). If any rule is unavailable the
    previous target is carried forward and ``held`` is True: missing data moves nothing.
    """
    out: list[tuple[float | None, bool]] = []
    previous = None
    for row in signals:
        if None in row:
            out.append((previous, previous is not None))
            continue
        previous = exposure_by_count[min(sum(row), len(exposure_by_count) - 1)]
        out.append((previous, False))
    return out


def equity_curve(opens: Sequence[float], closes: Sequence[float], targets: Sequence[float | None],
                 cost_rate: float) -> tuple[int, list[float], list[float]]:
    """Closing equity of the gated position and of buy and hold, both starting at 1.

    Returns (start, gated, held): ``start`` is the first session traded, the one after the
    first decision with a target. Both begin fully invested at that open. A change of target
    is traded at the next open and costs ``cost_rate`` times the amount traded. Cash earns
    nothing. Between changes the position is left alone, so its weight drifts with price.
    """
    first = next(i for i, t in enumerate(targets) if t is not None)
    start = first + 1
    units, cash, current = 1.0 / opens[start], 0.0, 1.0
    gated, held = [], []
    for s in range(start, len(closes)):
        wanted = targets[s - 1]
        if wanted != current:
            equity = cash + units * opens[s]
            cost = cost_rate * abs(wanted * equity - units * opens[s])
            units, cash, current = wanted * (equity - cost) / opens[s], (1.0 - wanted) * (equity - cost), wanted
        gated.append(cash + units * closes[s])
        held.append(closes[s] / opens[start])
    return start, gated, held


def max_drawdown(equity: Sequence[float]) -> float:
    """The largest fall from a running peak, as a negative fraction. The start value 1 counts as a peak."""
    peak, worst = 1.0, 0.0
    for value in equity:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def forward_drawdown(opens: Sequence[float], closes: Sequence[float], decision: int, horizon: int) -> float | None:
    """Lowest close of the ``horizon`` sessions after the decision, over the next open, minus one.

    None when the data ends before the horizon does. Positive if no close fell below that open.
    """
    if decision + horizon >= len(closes):
        return None
    return min(closes[decision + 1:decision + horizon + 1]) / opens[decision + 1] - 1.0


def episodes(targets: Sequence[float | None], merge_gap: int) -> list[tuple[int, int]]:
    """Runs of decisions with a target below 1, as (first, last). Runs fewer than ``merge_gap`` decisions apart are one."""
    runs: list[list[int]] = []
    for i, target in enumerate(targets):
        if target is None or target >= 1.0:
            continue
        if runs and i - runs[-1][1] - 1 < merge_gap:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [(a, b) for a, b in runs]


def reentry_ratios(opens: Sequence[float], runs: Sequence[tuple[int, int]]) -> tuple[list[float], int]:
    """For each finished episode, the open at which full exposure returns over the open at which it was first cut.

    Above 1 means the rule stepped aside and came back at a higher price: a whipsaw. Returns
    (ratios, unfinished), where unfinished episodes have no re-entry open inside the data.
    """
    ratios = [opens[last + 2] / opens[first + 1] for first, last in runs if last + 2 < len(opens)]
    return ratios, len(runs) - len(ratios)


def episode_interval(stressed: Sequence[Sequence[float]], ordinary: Sequence[Sequence[float]],
                     draws: int, seed: int, coverage: float = 0.90) -> tuple[float, float]:
    """An interval for mean(stressed sessions) minus mean(ordinary sessions), resampling whole runs.

    Each inner list is one run's session values. Runs, not sessions, are drawn with
    replacement, because sessions inside a run are not independent.
    """
    rng, results = random.Random(seed), []
    for _ in range(draws):
        a = [v for run in rng.choices(stressed, k=len(stressed)) for v in run]
        b = [v for run in rng.choices(ordinary, k=len(ordinary)) for v in run]
        results.append(fmean(a) - fmean(b))
    results.sort()
    tail = (1.0 - coverage) / 2.0
    return results[int(tail * draws)], results[int((1.0 - tail) * draws) - 1]
