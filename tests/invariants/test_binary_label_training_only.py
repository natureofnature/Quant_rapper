"""Invariant: a month's left-tail cut and model come from matured training outcomes only."""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_evaluator/src"))
try:
    import numpy as np
    from quant_evaluator.beta.model_gate import walk_forward_binary
    from quant_evaluator.beta.walkforward import monthly_folds
except ModuleNotFoundError:
    np = None

SESSIONS = [d for d in (date(2015, 1, 1) + timedelta(days=n) for n in range(1100)) if d.weekday() < 5]
TREE = {"n_estimators": 10, "min_child_days": 20}


@unittest.skipIf(np is None, "numpy or lightgbm is not installed")
class BinaryLabelTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.x = rng.normal(size=(len(SESSIONS), 3))
        self.y = list(self.x[:, 0] * 0.02 + rng.normal(scale=0.02, size=len(SESSIONS)))
        self.first = date(2017, 1, 1)

    def scores(self, outcomes, **settings):
        return walk_forward_binary(SESSIONS, self.x, outcomes, self.first, TREE, min_train=250, **settings)

    def test_outcomes_not_matured_before_a_month_change_none_of_its_scores(self):
        base = self.scores(self.y, worst_share=0.15)
        fold = monthly_folds(SESSIONS, self.first, min_train=250)[5]
        changed = list(self.y)
        for i in range(fold.train[-1] + 1, len(SESSIONS)):  # everything the month's model may not see
            changed[i] = -9.0
        again = self.scores(changed, worst_share=0.15)
        self.assertTrue(all(base[i] is not None for i in fold.test))
        self.assertEqual([base[i] for i in range(fold.test[-1] + 1)], [again[i] for i in range(fold.test[-1] + 1)])

    def test_an_earlier_outcome_does_move_the_cut(self):
        fold = monthly_folds(SESSIONS, self.first, min_train=250)[5]
        changed = list(self.y)
        for i in fold.train[:120]:
            changed[i] = -9.0
        self.assertNotEqual([self.scores(self.y, worst_share=0.15)[i] for i in fold.test],
                            [self.scores(changed, worst_share=0.15)[i] for i in fold.test])

    def test_a_decision_without_an_outcome_has_no_label_and_is_not_trained_on(self):
        holes = [None if i % 7 == 0 else v for i, v in enumerate(self.y)]
        scores = self.scores(holes, worst_share=0.15)
        self.assertTrue(all(scores[i] is not None for i in range(len(SESSIONS)) if SESSIONS[i] >= date(2018, 1, 1)))
        poisoned = [(-9.0 if i % 7 == 0 else v) for i, v in enumerate(self.y)]  # had the holes been read as outcomes, the cut would move
        self.assertNotEqual(scores, self.scores(poisoned, worst_share=0.15))

    def test_a_missing_feature_gets_no_forecast_and_given_labels_are_used_as_they_are(self):
        labels = [float(v < -0.02) for v in self.y]
        x = self.x.copy(); last = len(SESSIONS) - 1; x[last, 1] = np.nan
        scores = walk_forward_binary(SESSIONS, x, labels, self.first, TREE, min_train=250)
        self.assertIsNone(scores[last])
        self.assertTrue(0.0 <= scores[last - 1] <= 1.0)
        with self.assertRaises(ValueError):
            walk_forward_binary(SESSIONS, self.x, self.y, self.first, TREE, min_train=250)  # continuous values are not labels

    def test_too_few_left_tail_labels_means_no_forecast(self):
        self.assertTrue(all(s is None for s in self.scores([0.0] * len(SESSIONS))))


if __name__ == "__main__":
    unittest.main()
