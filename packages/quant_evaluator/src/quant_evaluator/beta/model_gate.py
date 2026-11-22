"""From a fitted model to a risk gate: walk-forward scores, gate states, and recall on the worst outcomes.

Pure arithmetic. Index i is a decision made after session i's close, as everywhere in this package.
"""

from collections.abc import Sequence
from datetime import date

import numpy as np

from quant_evaluator.beta.forward_target import HORIZON
from quant_evaluator.beta.ordinal_model import cumulative_probabilities, fit_linear_ordinal
from quant_evaluator.beta.walkforward import monthly_folds

GREEN, YELLOW, RED = 0, 1, 2


def walk_forward_left_tail(sessions: list[date], features: np.ndarray, classes: Sequence[int | None], first_test: date,
                           l2: float, tail_weight: float = 1.0, horizon: int = HORIZON, embargo: int = 0,
                           rolling_sessions: int | None = None, min_train: int = 250) -> list[float | None]:
    """Out-of-sample probability of the left-tail class for each decision, None where there is no forecast.

    Each month is scored by a model fitted only on decisions whose outcomes had matured
    before the month's first session. A training decision without a class or with a missing
    feature is left out; a test decision with a missing feature gets None.
    """
    x = np.asarray(features, dtype=float)
    y = np.array([np.nan if c is None else float(c) for c in classes])
    scores: list[float | None] = [None] * len(sessions)
    for fold in monthly_folds(sessions, first_test, horizon, embargo, rolling_sessions, min_train):
        train = np.array(fold.train)
        usable = np.isfinite(x[train]).all(axis=1) & np.isfinite(y[train])
        if usable.sum() < min_train:
            continue
        fit = fit_linear_ordinal(x[train], y[train], l2=l2, tail_weight=tail_weight)
        prob = cumulative_probabilities(fit, x[np.array(fold.test)])
        for index, row in zip(fold.test, prob):
            scores[index] = None if np.isnan(row[0]) else float(1.0 - row[0])
    return scores


def gate_states(scores: Sequence[float | None], red_quantile: float = 0.85, yellow_quantile: float = 0.70,
                window: int = 504, min_scores: int = 252, step_down_after: int = 3) -> list[tuple[int | None, bool]]:
    """Gate state per decision, as (state, held): GREEN 0, YELLOW 1, RED 2, or None before the gate starts.

    A score is compared with quantiles of the previous scores only: the last ``window`` of
    them, and at least ``min_scores``, never the score being judged. The gate takes its first
    state on a decision that has a score. The state rises at once. It falls only after
    ``step_down_after`` consecutive scored decisions whose own level is below it, and then to
    the highest level seen in that run. A decision without a score holds the previous state,
    is marked held once a state exists, and breaks the run: the count starts again.
    """
    if not 0 < yellow_quantile < red_quantile < 1 or step_down_after < 1 or min_scores < 2 or window < min_scores:
        raise ValueError("need 0 < yellow < red < 1, a positive step-down run, and window >= min_scores >= 2")
    past: list[float] = []
    out: list[tuple[int | None, bool]] = []
    state: int | None = None
    below: list[int] = []
    for score in scores:
        if score is None:
            out.append((state, state is not None))
            below = []  # not consecutive any more
            continue
        if len(past) >= min_scores:
            recent = np.array(past[-window:])
            level = RED if score >= np.quantile(recent, red_quantile) else \
                YELLOW if score >= np.quantile(recent, yellow_quantile) else GREEN
            if state is None or level >= state:
                state, below = level, []
            else:
                below.append(level)
                if len(below) >= step_down_after:
                    state, below = max(below), []
        out.append((state, False))
        past.append(score)
    return out


def tail_recall(outcomes: Sequence[float | None], alerts: Sequence[bool | None], worst_share: float = 0.15) -> dict:
    """How many of the worst outcomes were under an alert when the decision was made.

    Uses only decisions that have both an outcome and a gate state. The worst
    ``worst_share`` of those outcomes, lowest first, are the cases; recall is the share of
    them with an alert. ``alert_rate`` is the share of all such decisions under an alert, the
    figure recall has to beat to mean anything.
    """
    pairs = [(o, bool(a)) for o, a in zip(outcomes, alerts) if o is not None and a is not None]
    if not pairs:
        return {"decisions": 0, "cases": 0, "recall": None, "alert_rate": None}
    cases = max(1, int(len(pairs) * worst_share))
    worst = sorted(pairs, key=lambda p: p[0])[:cases]
    return {"decisions": len(pairs), "cases": cases, "recall": sum(a for _, a in worst) / cases,
            "alert_rate": sum(a for _, a in pairs) / len(pairs)}


def drawdown_episodes(outcomes: Sequence[float | None], usable: Sequence[bool], worst_share: float = 0.15,
                      merge_gap: int = 20) -> list[list[int]]:
    """The worst outcomes grouped into episodes: case decisions with fewer than ``merge_gap`` decisions between them are one.

    So with the default 20, cases at decisions 10 and 30 (19 between) are one episode and
    cases at 10 and 31 (20 between) are two. This is the same count the frozen rule
    baseline's episodes use. Cases are the worst ``worst_share`` of the outcomes at decisions marked usable. The
    episodes come from what happened, not from any model's alerts, so two models can be
    compared on exactly the same ones.
    """
    eligible = [i for i, (o, ok) in enumerate(zip(outcomes, usable)) if o is not None and ok]
    if not eligible:
        return []
    cases = sorted(sorted(eligible, key=lambda i: outcomes[i])[:max(1, int(len(eligible) * worst_share))])
    runs = [[cases[0]]]
    for index in cases[1:]:
        if index - runs[-1][-1] - 1 < merge_gap:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def paired_recall_difference(outcomes: Sequence[float | None], alerts_a: Sequence[bool | None], alerts_b: Sequence[bool | None],
                             draws: int, seed: int, worst_share: float = 0.15, merge_gap: int = 20,
                             coverage: float = 0.90) -> dict:
    """Recall of model B minus recall of model A on the same worst outcomes, with an interval from resampling whole episodes.

    Only decisions where both models have a gate state and the outcome exists are used.
    Each draw takes the realised drawdown episodes with replacement and computes both
    recalls on that one draw, so the difference is paired.
    """
    usable = [a is not None and b is not None for a, b in zip(alerts_a, alerts_b)]
    runs = drawdown_episodes(outcomes, usable, worst_share, merge_gap)
    cases = [i for run in runs for i in run]
    if not cases:
        return {"episodes": 0, "cases": 0, "recall_a": None, "recall_b": None, "difference": None, "interval": None}
    recall = lambda alerts, indices: sum(bool(alerts[i]) for i in indices) / len(indices)
    rng, differences = np.random.default_rng(seed), []
    for _ in range(draws):
        drawn = [i for k in rng.integers(0, len(runs), size=len(runs)) for i in runs[k]]
        differences.append(recall(alerts_b, drawn) - recall(alerts_a, drawn))
    tail = (1.0 - coverage) / 2.0
    low, high = np.quantile(differences, [tail, 1.0 - tail])
    a, b = recall(alerts_a, cases), recall(alerts_b, cases)
    return {"episodes": len(runs), "cases": len(cases), "recall_a": a, "recall_b": b, "difference": b - a,
            "interval": [float(low), float(high)]}
