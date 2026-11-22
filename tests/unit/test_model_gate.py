"""The model-driven gate: walk-forward scores read only the past, states use only earlier scores."""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_evaluator/src"))
try:
    import numpy as np
    from quant_evaluator.beta.risk_gate import episodes
    from quant_evaluator.beta.model_gate import (GREEN, RED, YELLOW, drawdown_episodes, gate_states, paired_recall_difference,
                                                 tail_recall, walk_forward_left_tail)
except ModuleNotFoundError:
    np = None

SESSIONS = [d for d in (date(2015, 1, 1) + timedelta(days=n) for n in range(1300)) if d.weekday() < 5]


@unittest.skipIf(np is None, "numpy is not installed")
class ModelGateTests(unittest.TestCase):
    def data(self):
        rng = np.random.default_rng(3)
        x = rng.normal(size=(len(SESSIONS), 3))
        classes = [int(c) for c in np.digitize(x[:, 0] + rng.logistic(size=len(SESSIONS)), [-1.5, 0.0, 1.5])]
        return x, classes

    def test_scores_for_a_month_do_not_change_when_anything_after_its_cutoff_changes(self):
        x, classes = self.data()
        first = date(2017, 6, 1)
        base = walk_forward_left_tail(SESSIONS, x, classes, first, l2=10.0)
        start = next(i for i, s in enumerate(SESSIONS) if s >= first)
        self.assertTrue(all(s is None for s in base[:start]) and all(s is not None for s in base[start:]))
        month_end = next(i for i, s in enumerate(SESSIONS) if s >= date(2017, 7, 1))
        later_classes = list(classes)
        for i in range(start - 20, len(classes)):  # outcomes not yet matured at the month's start
            later_classes[i] = 3 - later_classes[i]
        later_x = x.copy()
        later_x[month_end:] += 5.0  # and every later feature row
        changed = walk_forward_left_tail(SESSIONS, later_x, later_classes, first, l2=10.0)
        self.assertEqual(changed[start:month_end], base[start:month_end])
        self.assertNotEqual(changed[month_end:], base[month_end:])  # later months do see what has matured by then
        matured = list(classes)
        matured[start - 21] = 3 - matured[start - 21]  # the last outcome that had matured: this one is used
        self.assertNotEqual(walk_forward_left_tail(SESSIONS, x, matured, first, l2=10.0)[start], base[start])

    def test_missing_inputs_give_no_forecast_and_missing_outcomes_are_not_trained_on(self):
        x, classes = self.data()
        first = date(2017, 6, 1)
        start = next(i for i, s in enumerate(SESSIONS) if s >= first)
        holed = x.copy()
        holed[start + 2, 1] = np.nan
        scores = walk_forward_left_tail(SESSIONS, holed, classes, first, l2=10.0)
        self.assertIsNone(scores[start + 2])
        self.assertIsNotNone(scores[start + 3])
        gaps = [None if i % 7 == 0 else c for i, c in enumerate(classes)]
        self.assertTrue(all(s is not None for s in walk_forward_left_tail(SESSIONS, x, gaps, first, l2=10.0)[start:]))

    def test_gate_judges_a_score_against_earlier_scores_only_and_steps_down_slowly(self):
        quiet = [0.10, 0.11, 0.12, 0.13] * 3  # twelve scores to seed the quantiles
        states = gate_states(quiet + [0.50, 0.05, 0.05, 0.125, 0.05, 0.05, 0.05], min_scores=12, window=20, step_down_after=3)
        self.assertEqual(states[:12], [(None, False)] * 12)  # not started: too few earlier scores
        self.assertEqual([s for s, _ in states[12:]], [RED, RED, RED, YELLOW, YELLOW, YELLOW, GREEN])
        # 0.50 is RED at once. Two low days do not release it; the third does, to the highest level of the run.
        spike = gate_states(quiet + [0.50], min_scores=12, window=20)
        self.assertEqual(spike[-1], (RED, False))
        self.assertEqual(gate_states(quiet + [0.50, 9.9], min_scores=12, window=20)[12], spike[-1])  # a later score changes nothing earlier
        held = gate_states(quiet + [0.50, None, None, 0.05], min_scores=12, window=20)
        self.assertEqual(held[13:15], [(RED, True), (RED, True)])  # no score: hold, and say so
        # A missing forecast breaks the run of low days: RED, low, low, missing, low is still RED, and needs three more.
        broken = gate_states(quiet + [0.50, 0.05, 0.05, None, 0.05, 0.05, 0.05], min_scores=12, window=20, step_down_after=3)
        self.assertEqual([s for s, _ in broken[12:]], [RED, RED, RED, RED, RED, RED, GREEN])
        # The gate takes its first state only on a decision that has a score, and nothing is held before that.
        late = gate_states(quiet + [None, None, 0.50], min_scores=12, window=20)
        self.assertEqual(late[12:], [(None, False), (None, False), (RED, False)])
        self.assertEqual(gate_states([None, 0.1])[0], (None, False))
        with self.assertRaises(ValueError):
            gate_states(quiet, red_quantile=0.5, yellow_quantile=0.7)

    def test_recall_counts_alerts_on_the_worst_outcomes_among_decisions_that_have_both(self):
        outcomes = [-0.30, -0.20, -0.01, 0.00, 0.01, 0.02, 0.03, 0.04, 0.05, None]
        alerts = [True, False, True, False, False, False, False, False, False, True]
        result = tail_recall(outcomes, alerts, worst_share=0.25)
        self.assertEqual(result, {"decisions": 9, "cases": 2, "recall": 0.5, "alert_rate": 2 / 9})
        self.assertEqual(tail_recall([None], [True])["recall"], None)
        self.assertEqual(tail_recall([-0.1, 0.1], [None, False])["decisions"], 1)

    def test_recall_difference_is_paired_on_the_same_realised_episodes(self):
        outcomes = [0.0] * 200
        for start in (10, 60, 110, 160):  # four separate bad stretches of five decisions
            for i in range(start, start + 5):
                outcomes[i] = -0.2
        both = [True] * 200
        self.assertEqual([run[0] for run in drawdown_episodes(outcomes, both, worst_share=0.1)], [10, 60, 110, 160])
        self.assertEqual(len(drawdown_episodes(outcomes, both, worst_share=0.1, merge_gap=60)), 1)  # close stretches merge
        two = [0.0] * 100
        two[10] = two[30] = -0.2  # 19 decisions between them: one episode
        self.assertEqual(drawdown_episodes(two, [True] * 100, worst_share=0.02), [[10, 30]])
        two[30], two[31] = 0.0, -0.2  # 20 between them: two episodes
        self.assertEqual(drawdown_episodes(two, [True] * 100, worst_share=0.02), [[10], [31]])
        self.assertEqual(episodes([1.0 if i not in (10, 30) else 0.0 for i in range(40)], 20), [(10, 30)])  # as the frozen baseline counts
        self.assertEqual(episodes([1.0 if i not in (10, 31) else 0.0 for i in range(40)], 20), [(10, 10), (31, 31)])
        a = [False] * 200
        b = [i in range(10, 15) or i in range(60, 65) for i in range(200)]  # B alerts in two of the four, A in none
        result = paired_recall_difference(outcomes, a, b, draws=500, seed=1, worst_share=0.1)
        self.assertEqual((result["episodes"], result["cases"], result["recall_a"], result["recall_b"], result["difference"]),
                         (4, 20, 0.0, 0.5, 0.5))
        self.assertEqual(result, paired_recall_difference(outcomes, a, b, draws=500, seed=1, worst_share=0.1))
        self.assertTrue(0.0 <= result["interval"][0] <= 0.5 <= result["interval"][1] <= 1.0)
        same = paired_recall_difference(outcomes, b, b, draws=200, seed=1, worst_share=0.1)
        self.assertEqual((same["difference"], same["interval"]), (0.0, [0.0, 0.0]))  # identical models never differ
        gappy = [None if 10 <= i < 15 else v for i, v in enumerate(b)]  # where one model has no state, neither is scored
        self.assertEqual(paired_recall_difference(outcomes, a, gappy, draws=50, seed=1, worst_share=0.1)["cases"], 19)


if __name__ == "__main__":
    unittest.main()
