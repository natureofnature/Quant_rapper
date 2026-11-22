"""The exposure gate and its bookkeeping, on small hand-checked cases."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_evaluator/src"))
from quant_evaluator.beta.risk_gate import (episode_interval, episodes, equity_curve, forward_drawdown, gate_targets,
                                            max_drawdown, reentry_ratios)

MAP = (1.0, 0.5, 0.0)


class RiskGateTests(unittest.TestCase):
    def test_gate_starts_when_every_rule_is_available_and_maps_the_count(self):
        signals = [(False, None, False), (False, False, False), (True, False, False), (True, True, False), (True, True, True)]
        self.assertEqual(gate_targets(signals, MAP), [(None, False), (1.0, False), (0.5, False), (0.0, False), (0.0, False)])

    def test_missing_rule_holds_the_previous_target_until_all_are_back(self):
        signals = [(True, True, False), (True, None, False), (False, None, False), (True, False, False)]
        # two stressed gives 0; then one goes stale: stay at 0 even though the others would allow more
        self.assertEqual(gate_targets(signals, MAP), [(0.0, False), (0.0, True), (0.0, True), (0.5, False)])

    def test_equity_trades_at_the_next_open_and_pays_for_what_it_trades(self):
        opens, closes = [100.0, 100.0, 110.0, 99.0], [100.0, 110.0, 99.0, 99.0]
        start, gated, held = equity_curve(opens, closes, [1.0, 0.0, 0.0, 0.0], cost_rate=0.01)
        self.assertEqual((start, held), (1, [1.1, 0.99, 0.99]))
        # fully invested through session 1; sold at session 2's open of 110, paying 1% of the 1.1 traded
        self.assertEqual([round(v, 6) for v in gated], [1.1, 1.089, 1.089])
        half = equity_curve(opens, closes, [0.5, 0.5, 0.5, 0.5], cost_rate=0.0)[1]
        self.assertEqual([round(v, 6) for v in half], [1.05, 0.995, 0.995])  # half the units from the first open on
        self.assertAlmostEqual(max_drawdown([1.1, 0.99, 1.2, 0.9]), 0.9 / 1.2 - 1)
        self.assertAlmostEqual(max_drawdown([0.95, 1.0]), -0.05)  # the start counts as a peak

    def test_forward_drawdown_runs_from_the_next_open_over_the_next_closes(self):
        opens, closes = [10.0, 20.0, 0.0, 0.0], [10.0, 22.0, 18.0, 30.0]
        self.assertAlmostEqual(forward_drawdown(opens, closes, 0, 2), 18.0 / 20.0 - 1)
        self.assertIsNone(forward_drawdown(opens, closes, 2, 2))

    def test_episodes_merge_when_close_together_and_reentry_is_priced_at_the_open_after_the_decision(self):
        targets = [None, 1.0, 0.5, 0.0, 1.0, 0.5, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0]
        self.assertEqual(episodes(targets, merge_gap=2), [(2, 5), (9, 9)])
        self.assertEqual(episodes(targets, merge_gap=1), [(2, 3), (5, 5), (9, 9)])
        opens = [float(i) for i in range(1, 13)]
        ratios, unfinished = reentry_ratios(opens, [(2, 5), (9, 9), (10, 10)])
        self.assertEqual((ratios, unfinished), ([8.0 / 4.0, 12.0 / 11.0], 1))  # cut at open 3, back at open 7

    def test_interval_resamples_whole_runs_and_repeats_for_a_seed(self):
        stressed, ordinary = [[-0.10, -0.12], [-0.02]], [[-0.01], [-0.03, -0.02], [0.0]]
        first = episode_interval(stressed, ordinary, draws=500, seed=7)
        self.assertEqual(first, episode_interval(stressed, ordinary, draws=500, seed=7))
        self.assertLess(first[0], first[1])
        self.assertLessEqual(-0.11 - 0.0, first[0] + 1e-9)  # no draw can be below the most extreme runs


if __name__ == "__main__":
    unittest.main()
