"""Measures over a trailing window of one series. Pure arithmetic: the caller selects the window.

``values`` are ascending by session and end at the session the measure is read on. Each
function says how many values it needs for a lookback of k sessions.
"""

from collections.abc import Sequence
from statistics import fmean, stdev


def _level(values: Sequence[float]) -> float:
    return values[-1]


def _change(values: Sequence[float]) -> float:
    """Latest value minus the value k sessions earlier. For yields and spreads."""
    return values[-1] - values[0]


def _roc(values: Sequence[float]) -> float:
    """Latest value over the value k sessions earlier, minus one. For prices."""
    if values[0] <= 0:
        raise ValueError("the starting value is not positive")
    return values[-1] / values[0] - 1.0


def _realized_vol(values: Sequence[float]) -> float:
    """Sample standard deviation of the k one-session simple returns. Not annualised."""
    if min(values[:-1]) <= 0:
        raise ValueError("a value in the window is not positive")
    return stdev(b / a - 1.0 for a, b in zip(values, values[1:]))


def _trend_state(values: Sequence[float]) -> float:
    """Latest value over the mean of the last k values (itself included), minus one.

    Positive means above the k-session average. It describes the price; it measures no
    one's positioning.
    """
    mean = fmean(values)
    if mean <= 0:
        raise ValueError("the window mean is not positive")
    return values[-1] / mean - 1.0


# measure -> (function, values needed for a lookback of k)
MEASURES = {"level": (_level, lambda k: 1), "change": (_change, lambda k: k + 1), "roc": (_roc, lambda k: k + 1),
            "realized_vol": (_realized_vol, lambda k: k + 1), "trend_state": (_trend_state, lambda k: k)}


def sessions_needed(measure: str, lookback: int | None, rank: int | None) -> int:
    """How many trailing sessions the measure reads, with ``rank`` earlier values to rank against."""
    return MEASURES[measure][1](lookback) + (rank or 0)


def windowed_value(measure: str, lookback: int | None, rank: int | None, values: Sequence[float]) -> float:
    """The measure on the latest session, or its rank among its own ``rank`` previous values.

    The rank is the share of those previous values strictly below the latest one, from 0 to 1.
    The latest value is not among them. Raises ValueError where the measure is undefined.
    """
    function, needed = MEASURES[measure][0], MEASURES[measure][1](lookback)
    if len(values) != needed + (rank or 0):
        raise ValueError(f"expected {needed + (rank or 0)} values, got {len(values)}")
    if rank is None:
        return function(values)
    series = [function(values[start:start + needed]) for start in range(rank + 1)]
    return sum(v < series[-1] for v in series[:-1]) / rank
