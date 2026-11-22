"""Invariant: the ordinal model's cumulative probabilities never cross, on any row it scores."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_evaluator/src"))
try:
    import numpy as np
    from quant_evaluator.beta.ordinal_model import cumulative_probabilities, fit_linear_ordinal
    from quant_evaluator.beta.tree_model import fit_tree_ordinal, tree_cumulative_probabilities
except ModuleNotFoundError:
    np = None


@unittest.skipIf(np is None, "numpy is not installed")
class OrdinalMonotonicityTests(unittest.TestCase):
    def data(self, seed, rows=600, columns=4, signal=1.0):
        rng = np.random.default_rng(seed)
        x = rng.normal(size=(rows, columns))
        latent = signal * (x[:, 0] - 0.5 * x[:, 1]) + rng.logistic(size=rows)
        return x, np.digitize(latent, [-1.5, 0.0, 1.5])

    def test_probabilities_are_ordered_on_every_out_of_sample_row(self):
        for seed, tail_weight, signal in ((1, 1.0, 1.0), (2, 1.5, 1.0), (3, 1.0, 0.0), (4, 1.5, 0.0)):
            x, y = self.data(seed, signal=signal)
            fit = fit_linear_ordinal(x[:400], y[:400], tail_weight=tail_weight)
            wild = np.vstack([x[400:], 50.0 * x[400:420], -50.0 * x[400:420]])  # including points far outside training
            prob = cumulative_probabilities(fit, wild)
            self.assertTrue((np.diff(prob, axis=1) <= 0).all(), (seed, tail_weight))
            self.assertTrue(((prob >= 0) & (prob <= 1)).all())
            self.assertTrue((np.diff(fit.biases) <= 0).all())

    def test_thresholds_out_of_order_are_put_in_order_and_reported(self):
        # Almost no row is in the lowest class and many are in the next, with pure noise features: the first
        # question's threshold is then pushed by its tail weight, and the fit must still come out ordered.
        rng = np.random.default_rng(9)
        x = rng.normal(size=(300, 2))
        y = np.array([1] * 150 + [2] * 5 + [3] * 145)  # class 0 never occurs in training
        fit = fit_linear_ordinal(x, y, tail_weight=1.5)
        prob = cumulative_probabilities(fit, x)
        self.assertTrue((np.diff(prob, axis=1) <= 0).all())
        self.assertGreater(prob[:, 0].min(), 0.99)  # a class never seen is given almost no probability, finitely
        self.assertTrue(np.isfinite(fit.biases).all())

    def test_fit_is_deterministic_reads_only_its_training_rows_and_finds_the_direction(self):
        x, y = self.data(5, signal=2.0)
        first, again = fit_linear_ordinal(x[:400], y[:400]), fit_linear_ordinal(x[:400], y[:400])
        self.assertTrue((first.weights == again.weights).all() and (first.biases == again.biases).all())
        self.assertTrue(np.allclose(first.mean, x[:400].mean(axis=0)))  # standardised on the training set alone
        self.assertGreater(first.weights[0], 0)
        self.assertLess(first.weights[1], 0)
        self.assertLess(abs(first.weights[2]) + abs(first.weights[3]), abs(first.weights[0]))
        heavier = fit_linear_ordinal(x[:400], y[:400], l2=1000.0)
        self.assertLess(np.abs(heavier.weights).sum(), np.abs(first.weights).sum())  # the penalty shrinks the slope

    def test_missing_values_are_left_out_of_the_fit_and_get_no_probability(self):
        x, y = self.data(6)
        holed = x.copy()
        holed[:50, 1] = np.nan
        fit = fit_linear_ordinal(holed, y)
        self.assertEqual(fit.rows_used, len(y) - 50)
        clean = fit_linear_ordinal(x[50:], y[50:])
        self.assertTrue(np.allclose(fit.weights, clean.weights) and np.allclose(fit.biases, clean.biases))
        prob = cumulative_probabilities(fit, holed[:60])
        self.assertTrue(np.isnan(prob[:50]).all() and np.isfinite(prob[50:]).all())
        with self.assertRaises(ValueError):
            fit_linear_ordinal(x, np.where(y == 0, 7, y))  # a class outside the range

    def test_tree_model_probabilities_are_ordered_on_every_out_of_sample_row(self):
        for seed, signal in ((1, 1.0), (3, 0.0)):
            x, y = self.data(seed, rows=900, signal=signal)
            fit = fit_tree_ordinal(x[:700], y[:700])
            wild = np.vstack([x[700:], 50.0 * x[700:720], -50.0 * x[700:720]])
            prob = tree_cumulative_probabilities(fit, wild)
            self.assertTrue((np.diff(prob, axis=1) <= 1e-12).all(), seed)
            self.assertTrue(((prob >= 0) & (prob <= 1)).all())
        holed = x.copy()
        holed[705, 0] = np.nan
        self.assertTrue(np.isnan(tree_cumulative_probabilities(fit, holed[700:710])[5]).all())


if __name__ == "__main__":
    unittest.main()
