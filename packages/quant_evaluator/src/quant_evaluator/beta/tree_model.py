"""Model C: a small boosted-tree model on threshold-expanded rows, monotone in the threshold. No data access.

Each decision becomes K-1 rows, one per question "is the class at least k", with k as an
extra feature. A monotone constraint on k makes the predicted probability fall as k rises,
so the cumulative probabilities cannot cross, without assuming every question shares one
slope. Capacity is held down on purpose: the outcomes overlap, so the independent sample
is far smaller than the row count.
"""

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np


@dataclass(frozen=True)
class TreeFit:
    booster: lgb.Booster
    tasks: int
    rows_used: int
    min_child_samples: int
    row_weight: float  # weight of each expanded row: a day's rows sum to one


def _expand(x: np.ndarray, tasks: int) -> np.ndarray:
    """Each row repeated once per question, with the question number as the last column."""
    k = np.tile(np.arange(1, tasks + 1, dtype=float), len(x))
    return np.column_stack([np.repeat(x, tasks, axis=0), k])


def fit_tree_ordinal(features: np.ndarray, classes: np.ndarray, classes_count: int = 4, n_estimators: int = 60,
                     reg_lambda: float = 10.0, learning_rate: float = 0.05, min_child_days: int = 60,
                     colsample_bytree: float = 0.75, seed: int = 0) -> TreeFit:
    """Fit on the rows whose features and class are all present.

    A leaf must hold at least ``min_child_days`` times K-1 expanded rows, so that it spans at
    least that many different trading days however the questions fall. Each expanded row
    carries weight 1/(K-1), so a day counts once, as in the linear model. Trees are depth 2.
    The number of trees is fixed: nothing is stopped early on a validation slice.
    """
    x, y = np.asarray(features, dtype=float), np.asarray(classes, dtype=float)
    keep = np.isfinite(x).all(axis=1) & np.isfinite(y)
    x, y = x[keep], y[keep]
    tasks = classes_count - 1
    if len(y) == 0 or ((y < 0) | (y >= classes_count) | (y != np.round(y))).any():
        raise ValueError("no complete row to fit, or a class outside 0 .. classes_count - 1")
    expanded = _expand(x, tasks)
    target = (np.repeat(y, tasks) >= expanded[:, -1]).astype(float)
    child = min_child_days * tasks
    params = {"objective": "binary", "max_depth": 2, "num_leaves": 4, "learning_rate": learning_rate, "lambda_l2": reg_lambda,
              "feature_fraction": colsample_bytree, "min_data_in_leaf": child, "min_sum_hessian_in_leaf": 0.0,
              "monotone_constraints": [0] * x.shape[1] + [-1], "seed": seed, "deterministic": True, "force_row_wise": True,
              "num_threads": 1, "verbosity": -1}
    weight = np.full(len(target), 1.0 / tasks)
    data = lgb.Dataset(expanded, label=target, weight=weight)
    return TreeFit(booster=lgb.train(params, data, num_boost_round=n_estimators), tasks=tasks, rows_used=int(len(y)),
                   min_child_samples=child, row_weight=float(weight[0]))


def tree_cumulative_probabilities(fit: TreeFit, features: np.ndarray) -> np.ndarray:
    """P(class >= k) for k = 1 .. K-1, one row per input row. A row with a missing feature gives NaN, not a guess."""
    x = np.asarray(features, dtype=float)
    out = fit.booster.predict(_expand(np.nan_to_num(x), fit.tasks)).reshape(len(x), fit.tasks)
    out[~np.isfinite(x).all(axis=1)] = np.nan
    return out


def smallest_leaf_in_days(fit: TreeFit, features: np.ndarray) -> int:
    """Over every tree and leaf, the smallest number of different training days a leaf holds."""
    x = np.asarray(features, dtype=float)
    x = x[np.isfinite(x).all(axis=1)]
    leaves = fit.booster.predict(_expand(x, fit.tasks), pred_leaf=True)
    day = np.repeat(np.arange(len(x)), fit.tasks)
    return min(len(set(day[leaves[:, tree] == leaf])) for tree in range(leaves.shape[1]) for leaf in set(leaves[:, tree]))
