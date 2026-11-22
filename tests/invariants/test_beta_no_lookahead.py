"""Invariant: a pre-event beta reads only sessions before the event window, and the abnormal return uses that beta."""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data", "quant_causal_graph", "quant_evaluator", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_causal_graph.recipes import event_state_recipe, project_leads
    from quant_contracts.evaluation import EventTime, Factor, ReturnPair
    from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
    from quant_evaluator.alpha.event_window import beta_before
    from quant_loop.event_table import build_table
    from quant_pit_data.asof import observe
except ModuleNotFoundError:
    SeriesBinding = None

NEW_YORK = ZoneInfo("America/New_York")
DAYS = [d for d in (date(2026, 6, 1) + timedelta(days=n) for n in range(120)) if d.weekday() < 5]


@unittest.skipIf(SeriesBinding is None, "pydantic is not installed")
class BetaNoLookaheadTests(unittest.TestCase):
    def setUp(self):
        def binding(series_id, value_time):
            return SeriesBinding(series_id=series_id, label=series_id, anchors=("test",), source_url="https://example.invalid",
                                 availability="official_archive", timezone="America/New_York", value_time=value_time,
                                 publication_delay_minutes=15, availability_basis="assumed", revisable=False, basis_note="Synthetic.")
        self.subject, self.benchmark = (binding("s:open", "09:30"), binding("s:close", "16:00")), (binding("b:open", "09:30"), binding("b:close", "16:00"))
        self.state = binding("x", "16:15")
        self.calendar = SessionCalendar(calendar_id="t", timezone="America/New_York", first=DAYS[0], last=DAYS[-1], sessions=tuple(DAYS), basis_note="Synthetic.")
        # The benchmark zigzags; the subject moves exactly twice as much each day, so its beta is 2.
        bench = [100.0]
        for n in range(1, len(DAYS)):
            bench.append(bench[-1] * (1.01 if n % 2 else 0.995))
        subj = [50.0]
        for n in range(1, len(DAYS)):
            subj.append(subj[-1] * (1 + 2 * (bench[n] / bench[n - 1] - 1)))
        self.bench, self.subj = bench, subj
        relationships = ROOT / "knowledge_base/prior_knowledge/Relationships"
        frozen = datetime(2026, 11, 21, 13, 23, tzinfo=NEW_YORK)
        lead = project_leads(relationships, "semiconductor", "TSM", frozen)["lead:economy:risk_appetite:sector_beta:sector"]
        self.recipe = event_state_recipe("recipe:test", [lead], frozen, "Synthetic", "filing", "SOXX", 5, [self.state, *self.subject, *self.benchmark])

    def rows(self, subj=None, bench=None):
        subj, bench = subj or self.subj, bench or self.bench
        out = [observe(self.state, d, 1.0) for d in DAYS]
        for d, s, b in zip(DAYS, subj, bench):
            out += [observe(self.subject[0], d, s), observe(self.subject[1], d, s), observe(self.benchmark[0], d, b), observe(self.benchmark[1], d, b)]
        return out

    def row(self, entry_index, beta_sessions=20, **prices):
        when = datetime.combine(DAYS[entry_index], datetime.min.time(), NEW_YORK) + timedelta(hours=8)
        event = EventTime(event_id="e", cutoff=Cutoff(time=when, basis="exact", source="Synthetic."))
        factor = Factor(factor_id="x", measure="level", series_ids=("x",), max_age_days=5)
        return build_table(self.recipe, [event], [factor], {"x": self.state}, self.subject, self.benchmark, self.rows(**prices),
                           self.calendar, {}, {"synthetic": "0" * 64}, beta_sessions=beta_sessions).rows[0]

    def test_beta_uses_only_sessions_before_the_entry_and_is_blind_to_the_window(self):
        entry = 60
        base = self.row(entry)
        self.assertEqual(base.entry_session, DAYS[entry])
        self.assertAlmostEqual(base.window.pre_beta, 2.0)
        self.assertAlmostEqual(base.window.beta_adjusted, base.window.subject - 2.0 * base.window.benchmark)
        self.assertEqual(base.reaction.pre_beta, base.window.pre_beta)
        for index in range(entry, entry + 6):  # any price at or after the entry leaves the beta as it was
            moved = list(self.subj)
            moved[index] *= 1.5
            self.assertEqual(self.row(entry, subj=moved).window.pre_beta, base.window.pre_beta, index)
        inside = list(self.subj)
        inside[entry - 5] *= 1.5  # a price inside the estimation window does change it
        self.assertNotEqual(self.row(entry, subj=inside).window.pre_beta, base.window.pre_beta)
        before = list(self.subj)
        before[entry - 22] *= 1.5  # one session before the 20-return window: not read
        self.assertEqual(self.row(entry, subj=before).window.pre_beta, base.window.pre_beta)

    def test_incomplete_history_gives_no_beta_and_says_so(self):
        early = self.row(10)  # only ten earlier sessions for a 20-return estimate
        self.assertEqual((early.window.pre_beta, early.window.beta_adjusted, early.status), (None, None, "complete"))
        self.assertIn("no pre-event beta", " ".join(early.flags))
        self.assertIsNotNone(early.window.relative)  # the unadjusted return is still there
        self.assertIsNone(self.row(60, beta_sessions=None).window.pre_beta)

    def test_arithmetic_and_edges_of_the_estimate(self):
        self.assertAlmostEqual(beta_before(self.subj, self.bench, 40, 20), 2.0)
        self.assertIsNone(beta_before(self.subj, self.bench, 19, 20))  # one return short
        self.assertIsNone(beta_before(self.subj, self.bench, len(self.subj), 20))  # past the end
        self.assertIsNone(beta_before(self.subj, [100.0] * len(self.subj), 40, 20))  # a benchmark that never moved
        holed = list(self.bench)
        holed[30] = None
        self.assertIsNone(beta_before(self.subj, holed, 40, 20))
        pair = ReturnPair(subject=0.05, benchmark=0.02, pre_beta=1.5)
        self.assertEqual(ReturnPair.model_validate_json(pair.model_dump_json()), pair)  # and a saved pair loads back
        with self.assertRaises(ValueError):
            ReturnPair.model_validate({**pair.model_dump(), "beta_adjusted": 0.0})


if __name__ == "__main__":
    unittest.main()
