"""Monthly walk-forward folds with an exact purge. Pure arithmetic over a list of sessions.

A model that forecasts the sessions of one month is fitted on decisions whose targets had
fully matured before that month began. Purging by one session too few lets the first test
day's close into training.
"""

from dataclasses import dataclass
from datetime import date

from quant_evaluator.beta.forward_target import HORIZON, target_end


@dataclass(frozen=True)
class Fold:
    """One refit: train on ``train`` (decision indices), then forecast ``test`` (the month's decisions)."""

    month: tuple[int, int]
    train: tuple[int, ...]
    test: tuple[int, ...]


def monthly_folds(sessions: list[date], first_test: date, horizon: int = HORIZON, embargo: int = 0,
                  rolling_sessions: int | None = None, min_train: int = 250) -> list[Fold]:
    """One fold per calendar month that has a session on or after ``first_test``.

    A month's model is frozen before the month's first session in ``sessions``, the full
    calendar. With that session at index T, a decision t may be trained on only if
    ``target_end(t) <= T - 1 - embargo``, that is t <= T - 1 - horizon - embargo; with the
    default 20-session target this is t <= T - 21. ``first_test`` only chooses which
    sessions are forecast: starting mid-month does not let that month's model see labels
    that matured during the month.

    ``sessions`` must be strictly increasing; an unordered calendar would put later
    observations into earlier folds. ``rolling_sessions`` keeps only the most recent that
    many training decisions; None is an expanding window. A month with fewer than
    ``min_train`` training decisions is skipped. Test decisions whose own target runs past
    the data are still listed: a forecast can be made for them, though not yet scored.
    """
    if embargo < 0 or horizon <= 0:
        raise ValueError("horizon must be positive and embargo non-negative")
    if min_train <= 0 or (rolling_sessions is not None and rolling_sessions <= 0):
        raise ValueError("min_train must be positive, and rolling_sessions positive when given")
    if any(a >= b for a, b in zip(sessions, sessions[1:])):
        raise ValueError("sessions must be unique and strictly increasing")
    month_start: dict[tuple[int, int], int] = {}
    tests: dict[tuple[int, int], list[int]] = {}
    for index, session in enumerate(sessions):
        month = (session.year, session.month)
        month_start.setdefault(month, index)
        if session >= first_test:
            tests.setdefault(month, []).append(index)
    folds = []
    for month, test in tests.items():
        start = month_start[month]
        last_train = start - 1 - horizon - embargo
        if last_train < 0:
            continue
        assert target_end(last_train, horizon) <= start - 1 - embargo
        first_train = 0 if rolling_sessions is None else max(0, last_train - rolling_sessions + 1)
        train = tuple(range(first_train, last_train + 1))
        if len(train) >= min_train:
            folds.append(Fold(month=month, train=train, test=tuple(test)))
    return folds
