"""Model A: a linear ordinal model with one shared slope and ordered thresholds. No data access.

For ordered classes 0 .. K-1 it models the K-1 cumulative probabilities
    P(class >= k) = sigmoid(x . w + b_k),   k = 1 .. K-1,
with the same weights w for every k. It is fitted by penalised maximum likelihood on the
K-1 binary questions "is the class at least k". The slope is shrunk by an L2 penalty; the
thresholds are not. The fit is deterministic: Newton steps from zero, no random start.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class OrdinalFit:
    """A fitted model. ``mean`` and ``scale`` are the training set's own, so applying it reads nothing from later rows."""

    mean: np.ndarray
    scale: np.ndarray
    weights: np.ndarray  # on standardised features
    biases: np.ndarray  # b_1 >= b_2 >= ... , one per cumulative question
    rows_used: int
    reordered: bool  # True if the fitted thresholds had to be put in order


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.tanh(0.5 * z))


def fit_linear_ordinal(features: np.ndarray, classes: np.ndarray, classes_count: int = 4, l2: float = 1.0,
                       tail_weight: float = 1.0, iterations: int = 100) -> OrdinalFit:
    """Fit on the rows whose features and class are all present; a row with a missing value is left out.

    ``tail_weight`` multiplies the loss of the first question, "is the class above the left
    tail"; 1.0 treats every question alike. Rows are weighted equally: an extreme outcome
    counts no more than a quiet one.
    """
    x, y = np.asarray(features, dtype=float), np.asarray(classes, dtype=float)
    if x.ndim != 2 or y.shape != (x.shape[0],):
        raise ValueError("features must be rows by columns, with one class per row")
    if classes_count < 2 or l2 <= 0 or tail_weight <= 0:
        raise ValueError("need at least two classes, and a positive penalty and tail weight")
    keep = np.isfinite(x).all(axis=1) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if len(y) == 0 or ((y < 0) | (y >= classes_count) | (y != np.round(y))).any():
        raise ValueError("no complete row to fit, or a class outside 0 .. classes_count - 1")
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    z = (x - mean) / scale
    n, p, tasks = z.shape[0], z.shape[1], classes_count - 1
    targets = (y[:, None] >= np.arange(1, classes_count)[None, :]).astype(float)  # n by tasks
    task_weight = np.ones(tasks)
    task_weight[0] = tail_weight
    penalty = np.concatenate([np.full(p, l2), np.full(tasks, 1e-6)])  # a trace on the thresholds keeps them finite
    theta = np.zeros(p + tasks)

    def loss_and_parts(t):
        scores = (z @ t[:p])[:, None] + t[p:][None, :]
        prob = _sigmoid(scores)
        log_loss = -(targets * np.log(np.clip(prob, 1e-300, None)) + (1 - targets) * np.log(np.clip(1 - prob, 1e-300, None)))
        return float((log_loss * task_weight).sum() + 0.5 * (penalty * t * t).sum()), prob

    value, prob = loss_and_parts(theta)
    for _ in range(iterations):
        residual = (prob - targets) * task_weight
        gradient = np.concatenate([z.T @ residual.sum(axis=1), residual.sum(axis=0)]) + penalty * theta
        curve = prob * (1 - prob) * task_weight
        hessian = np.zeros((p + tasks, p + tasks))
        hessian[:p, :p] = (z * curve.sum(axis=1)[:, None]).T @ z
        hessian[:p, p:] = z.T @ curve
        hessian[p:, :p] = hessian[:p, p:].T
        hessian[p:, p:] = np.diag(curve.sum(axis=0))
        hessian += np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        size = 1.0
        while True:  # halve the step until the loss does not rise; the problem is convex, so this ends
            candidate = theta - size * step
            new_value, new_prob = loss_and_parts(candidate)
            if new_value <= value + 1e-12 or size < 1e-8:
                break
            size *= 0.5
        moved = float(np.abs(candidate - theta).max())
        theta, value, prob = candidate, new_value, new_prob
        if moved < 1e-10:
            break
    biases = np.minimum.accumulate(theta[p:])  # enforce b_1 >= b_2 >= ...: cumulative probabilities never cross
    return OrdinalFit(mean=mean, scale=scale, weights=theta[:p].copy(), biases=biases, rows_used=int(n),
                      reordered=bool((biases != theta[p:]).any()))


def cumulative_probabilities(fit: OrdinalFit, features: np.ndarray) -> np.ndarray:
    """P(class >= k) for k = 1 .. K-1, one row per input row. A row with a missing feature gives NaN, not a guess."""
    x = np.asarray(features, dtype=float)
    scores = (((x - fit.mean) / fit.scale) @ fit.weights)[:, None] + fit.biases[None, :]
    out = _sigmoid(scores)
    out[~np.isfinite(x).all(axis=1)] = np.nan
    return out
