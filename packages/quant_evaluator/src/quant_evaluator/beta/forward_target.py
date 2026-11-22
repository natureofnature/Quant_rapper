"""The tradeable forward target of the daily risk model, and its ordered classes. Pure arithmetic.

Index t is a decision made after session t's close. Nothing is traded before the open of
session t + 1, so the target starts there and never uses the close of session t as a price.
"""

from collections.abc import Sequence
from math import isfinite
from statistics import fmean, stdev

HORIZON = 20


def usable(price: float | None) -> bool:
    """A price that can be divided by and averaged: present, finite and positive."""
    return price is not None and isfinite(price) and price > 0


def target_end(decision: int, horizon: int = HORIZON) -> int:
    """Index of the last session whose close the target of ``decision`` needs."""
    return decision + horizon


def forward_average_return(opens: Sequence[float], closes: Sequence[float], decision: int,
                           horizon: int = HORIZON) -> float | None:
    """Mean close of sessions t+1 .. t+horizon over the open of session t+1, minus one.

    None when the data ends before ``target_end`` or the entry open is beyond the series, or when the entry open or any close of the
    window is missing, not finite or not positive: an outcome that cannot be computed is
    missing, never a number. A fall followed by a recovery can leave this near zero: it is
    an average level, not a drawdown.
    """
    if decision < 0 or horizon <= 0 or target_end(decision, horizon) >= len(closes) or decision + 1 >= len(opens):
        return None
    window = closes[decision + 1:decision + horizon + 1]
    if len(window) != horizon or not usable(opens[decision + 1]) or not all(usable(c) for c in window):
        return None
    return fmean(window) / opens[decision + 1] - 1.0


def trailing_volatility(closes: Sequence[float], decision: int, sessions: int = 20) -> float | None:
    """Sample standard deviation of the last ``sessions`` close-to-close returns ending at session t.

    Uses closes up to and including session t, all known at the decision. None without enough
    history, or when a close in the window is missing, not finite or not positive.
    """
    if sessions < 2 or decision < sessions or decision >= len(closes):
        return None
    window = closes[decision - sessions:decision + 1]
    if len(window) != sessions + 1 or not all(usable(c) for c in window):
        return None
    return stdev(b / a - 1.0 for a, b in zip(window, window[1:]))


def class_thresholds(daily_volatility: float, floor: float, cap: float, horizon: int = HORIZON) -> tuple[float, float, float]:
    """The three cut points (r1, 0, r3) with r1 = -clip(0.8 * sigma, floor, cap) and r3 = -r1.

    sigma is the daily volatility scaled to the forward average: 0.6 * daily * sqrt(horizon).
    The clip keeps the left-tail cut from loosening in a crash or tightening in a calm.
    """
    if not (isfinite(daily_volatility) and daily_volatility >= 0 and isfinite(floor) and isfinite(cap) and 0 < floor <= cap):
        raise ValueError("the volatility must be finite and non-negative, and the bounds satisfy 0 < floor <= cap")
    scaled = 0.6 * daily_volatility * horizon ** 0.5
    left = -min(max(0.8 * scaled, floor), cap)
    return left, 0.0, -left


def ordinal_class(value: float, thresholds: tuple[float, float, float]) -> int:
    """0 left tail (below r1), 1 mild negative, 2 mild positive, 3 right tail (at or above r3).

    A value or threshold that is not a finite number is refused. Comparing it would fail every
    test and put a missing outcome in class 0, the most severe one.
    """
    if value is None or not isfinite(value) or not all(isfinite(cut) for cut in thresholds):
        raise ValueError("a class needs a finite value and finite thresholds; a missing outcome has no class")
    if list(thresholds) != sorted(set(thresholds)):
        raise ValueError("the thresholds must be strictly ascending")
    return sum(value >= cut for cut in thresholds)
