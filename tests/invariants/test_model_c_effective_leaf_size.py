"""Invariant: every leaf of the tree model spans at least 60 different trading days, and a day counts once."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_evaluator/src"))
try:
    import numpy as np
    from quant_evaluator.beta.tree_model import _expand, fit_tree_ordinal, smallest_leaf_in_days, tree_cumulative_probabilities
except ModuleNotFoundError:
    np = None


@unittest.skipIf(np is None, "numpy or lightgbm is not installed")
class ModelCLeafSizeTests(unittest.TestCase):
    def data(self, seed, rows=1200):
        rng = np.random.default_rng(seed)
        x = rng.normal(size=(rows, 3))
        return x, np.digitize(x[:, 0] - x[:, 1] ** 2 + rng.logistic(size=rows), [-2.0, -0.5, 1.0])

    def test_every_leaf_spans_at_least_sixty_trading_days(self):
        for seed in (1, 2, 3):
            x, y = self.data(seed)
            fit = fit_tree_ordinal(x, y)
            self.assertEqual(fit.min_child_samples, 180)  # 60 days times the three questions
            self.assertGreaterEqual(smallest_leaf_in_days(fit, x), 60)
            self.assertLessEqual(max(t["num_leaves"] for t in fit.booster.dump_model()["tree_info"]), 4)

    def test_expansion_gives_each_day_three_rows_of_weight_one_third(self):
        x, y = self.data(4, rows=300)
        expanded = _expand(x, 3)
        self.assertEqual(expanded.shape, (900, 4))
        self.assertTrue((expanded[:3, :3] == x[0]).all() and list(expanded[:3, 3]) == [1.0, 2.0, 3.0])
        fit = fit_tree_ordinal(x, y, n_estimators=5)
        self.assertEqual(fit.rows_used, 300)
        self.assertAlmostEqual(fit.row_weight * 3, 1.0)  # a day's three rows sum to one, so 300 days weigh 300

    def test_fit_is_deterministic_and_too_little_data_grows_no_split(self):
        x, y = self.data(5)
        first, again = fit_tree_ordinal(x, y), fit_tree_ordinal(x, y)
        self.assertTrue((tree_cumulative_probabilities(first, x) == tree_cumulative_probabilities(again, x)).all())
        small = fit_tree_ordinal(x[:100], y[:100])  # 300 expanded rows cannot make two leaves of 180
        self.assertEqual(max(t["num_leaves"] for t in small.booster.dump_model()["tree_info"]), 1)
        self.assertEqual(len(set(np.round(tree_cumulative_probabilities(small, x[:50]), 12).ravel())), 1)


if __name__ == "__main__":
    unittest.main()
