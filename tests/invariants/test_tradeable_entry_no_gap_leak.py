"""Invariant: the target starts at the next open and its classes use only what was known at the decision."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_evaluator/src"))
from quant_evaluator.beta.forward_target import (class_thresholds, forward_average_return, ordinal_class, target_end,
                                                 trailing_volatility)


class TradeableEntryTests(unittest.TestCase):
    def setUp(self):
        self.opens = [100.0 + i for i in range(60)]
        self.closes = [100.5 + i for i in range(60)]

    def test_target_is_measured_from_the_next_open_and_ignores_the_decision_day_close(self):
        value = forward_average_return(self.opens, self.closes, 10)
        self.assertAlmostEqual(value, sum(self.closes[11:31]) / 20 / self.opens[11] - 1)
        gapped = list(self.closes)
        gapped[10] = 1.0  # a wild close on the decision day itself changes nothing
        self.assertEqual(forward_average_return(self.opens, gapped, 10), value)
        moved = list(self.opens)
        moved[11] = 50.0  # the entry price is the next open
        self.assertNotEqual(forward_average_return(moved, self.closes, 10), value)
        for index in (10, 31, 40):  # nothing at or before the decision, and nothing after the horizon, is read
            if index != 11:
                changed = list(self.opens)
                changed[index] = 1.0
                self.assertEqual(forward_average_return(changed, self.closes, 10), value)
        late = list(self.closes)
        late[31] = 1.0
        self.assertEqual(forward_average_return(self.opens, late, 10), value)
        self.assertEqual(target_end(10), 30)

    def test_target_that_runs_past_the_data_is_missing_not_shortened(self):
        self.assertIsNotNone(forward_average_return(self.opens, self.closes, 39))
        self.assertIsNone(forward_average_return(self.opens, self.closes, 40))

    def test_missing_or_broken_price_makes_the_outcome_missing_and_never_a_class(self):
        nan = float("nan")
        for bad in (nan, None, 0.0, -1.0, float("inf")):
            closes = list(self.closes)
            closes[20] = bad  # inside the forward window of decision 10
            self.assertIsNone(forward_average_return(self.opens, closes, 10), bad)
            opens = list(self.opens)
            opens[11] = bad
            self.assertIsNone(forward_average_return(opens, self.closes, 10), bad)
            self.assertIsNone(trailing_volatility(closes, 30), bad)
        thresholds = class_thresholds(0.008, 0.04, 0.07)
        for bad in (nan, None, float("inf")):
            with self.assertRaisesRegex(ValueError, "a missing outcome has no class"):
                ordinal_class(bad, thresholds)  # it used to fall into class 0, the most severe
        with self.assertRaises(ValueError):
            ordinal_class(0.0, (nan, 0.0, 0.04))
        with self.assertRaisesRegex(ValueError, "strictly ascending"):
            ordinal_class(0.0, (0.04, 0.0, -0.04))
        with self.assertRaises(ValueError):
            class_thresholds(nan, 0.04, 0.07)

    def test_window_at_the_edge_of_the_series_is_missing_and_never_shortened_or_an_error(self):
        closes = self.closes[:21]
        self.assertIsNotNone(trailing_volatility(closes, 20))  # exactly enough: 21 closes give 20 returns
        self.assertIsNone(trailing_volatility(closes, 21))  # one past the end would silently use 19
        self.assertIsNone(trailing_volatility(closes, 500))
        self.assertIsNone(trailing_volatility(closes, 20, sessions=1))
        self.assertIsNone(forward_average_return(self.opens[:11], self.closes, 10))  # the entry open is beyond the opens
        self.assertIsNotNone(forward_average_return(self.opens[:12], self.closes, 10))
        self.assertIsNone(forward_average_return(self.opens, self.closes, -1))
        self.assertIsNone(forward_average_return(self.opens, self.closes, 10, horizon=0))

    def test_volatility_behind_the_classes_ends_at_the_decision(self):
        value = trailing_volatility(self.closes, 30)
        future = list(self.closes)
        future[31] = 1.0
        self.assertEqual(trailing_volatility(future, 30), value)
        self.assertIsNone(trailing_volatility(self.closes, 19))
        today = list(self.closes)
        today[30] = 200.0
        self.assertNotEqual(trailing_volatility(today, 30), value)  # the decision day's own close is known and used

    def test_thresholds_are_clipped_and_classes_are_ordered(self):
        calm, crash = class_thresholds(0.008, 0.04, 0.07), class_thresholds(0.04, 0.04, 0.07)
        self.assertEqual((calm, crash), ((-0.04, 0.0, 0.04), (-0.07, 0.0, 0.07)))  # floor in a calm, cap in a crash
        middle = class_thresholds(0.025, 0.04, 0.07)
        self.assertAlmostEqual(middle[0], -0.8 * 0.6 * 0.025 * 20 ** 0.5)
        self.assertEqual([ordinal_class(v, calm) for v in (-0.05, -0.04, -0.01, 0.0, 0.039, 0.04)], [0, 1, 1, 2, 2, 3])
        with self.assertRaises(ValueError):
            class_thresholds(0.01, 0.07, 0.04)


if __name__ == "__main__":
    unittest.main()
