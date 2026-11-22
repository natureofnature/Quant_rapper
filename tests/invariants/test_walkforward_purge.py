"""Invariant: no training target overlaps the month being forecast."""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_evaluator/src"))
from quant_evaluator.beta.forward_target import target_end
from quant_evaluator.beta.walkforward import monthly_folds

SESSIONS = [d for d in (date(2015, 1, 1) + timedelta(days=n) for n in range(1500)) if d.weekday() < 5]


class WalkForwardPurgeTests(unittest.TestCase):
    def test_every_training_target_matures_before_the_test_month_in_every_fold(self):
        for horizon, embargo, rolling in ((20, 0, None), (20, 5, None), (20, 0, 500), (21, 0, None), (5, 3, 300)):
            folds = monthly_folds(SESSIONS, date(2017, 1, 1), horizon=horizon, embargo=embargo, rolling_sessions=rolling)
            self.assertGreater(len(folds), 20)
            for fold in folds:
                start = fold.test[0]  # first_test is a month start here, so this is the month's first session
                self.assertTrue(all(target_end(t, horizon) < start - embargo for t in fold.train))
                self.assertEqual(fold.train[-1], start - 1 - horizon - embargo)  # and nothing usable is thrown away
                self.assertEqual({(SESSIONS[i].year, SESSIONS[i].month) for i in fold.test}, {fold.month})
                if rolling:
                    self.assertEqual(len(fold.train), rolling)
                else:
                    self.assertEqual(fold.train[0], 0)

    def test_default_cutoff_is_twenty_one_sessions_before_the_month_not_twenty(self):
        fold = monthly_folds(SESSIONS, date(2017, 1, 1))[0]
        start = fold.test[0]
        self.assertEqual(fold.train[-1], start - 21)
        self.assertEqual(target_end(start - 21), start - 1)  # its last close is the session before the month
        self.assertEqual(target_end(start - 20), start)  # one session later would use the month's first close

    def test_model_is_frozen_at_the_start_of_its_month_wherever_forecasting_starts(self):
        whole = monthly_folds(SESSIONS, date(2017, 11, 1))[0]
        partial = monthly_folds(SESSIONS, date(2017, 11, 16))[0]
        self.assertEqual((partial.month, partial.train), ((2017, 11), whole.train))  # the same training set
        self.assertEqual(SESSIONS[partial.test[0]], date(2017, 11, 16))  # only the forecast days differ
        self.assertLess(partial.test[0] - 21, partial.test[0])
        self.assertLess(partial.train[-1], partial.test[0] - 21)  # so it stops well short of a mid-month cutoff
        self.assertEqual(SESSIONS[target_end(partial.train[-1])], date(2017, 10, 31))  # its last label matured in October

    def test_unordered_or_repeated_sessions_are_refused(self):
        shuffled = SESSIONS[:700] + [SESSIONS[800]] + SESSIONS[700:800] + SESSIONS[801:]
        for bad in (shuffled, SESSIONS + [SESSIONS[-1]], list(reversed(SESSIONS))):
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                monthly_folds(bad, date(2017, 1, 1))
        for bad in ({"rolling_sessions": 0}, {"rolling_sessions": -5}, {"min_train": 0}):
            with self.assertRaisesRegex(ValueError, "must be positive"):
                monthly_folds(SESSIONS, date(2017, 1, 1), **bad)

    def test_folds_are_consecutive_months_and_test_sessions_are_never_reused(self):
        folds = monthly_folds(SESSIONS, date(2017, 1, 1))
        tested = [i for fold in folds for i in fold.test]
        self.assertEqual(tested, sorted(set(tested)))
        self.assertEqual(tested, list(range(tested[0], len(SESSIONS))))
        self.assertEqual(monthly_folds(SESSIONS[:100], date(2015, 2, 1)), [])  # too little history to train on


if __name__ == "__main__":
    unittest.main()
