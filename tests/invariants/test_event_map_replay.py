"""Invariant: an event map shows a decision only what was available before it, and replays exactly.

Uses the tracked pilot: TSMC's October 2026 revenue release at two decision times that
bracket the release's dated source. Skipped when pydantic is not installed.
"""

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data", "quant_causal_graph", "quant_evaluator", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_causal_graph.event_map import bindings_digest, build_from_files
    from quant_contracts.evaluation import EventTime, Factor
    from quant_contracts.event_map import EventMap, SourcePacket
    from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
    from quant_loop.event_table import build_table
    from quant_pit_data.asof import observe
except ModuleNotFoundError:
    EventMap = None

PILOT = ROOT / "artifacts/event_maps/tsmc_monthly_revenue_2026-10"
NEW_YORK = ZoneInfo("America/New_York")
BEFORE, AFTER = (datetime(2026, 11, day, 8, 0, tzinfo=NEW_YORK) for day in (10, 11))
RELEASE = "ev:tsmc:monthly_revenue:2026-10:release"
SERIES = "tsmc:monthly_revenue:yoy_pct_as_released"


@unittest.skipIf(EventMap is None, "pydantic is not installed")
class EventMapReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before, cls.after = (build_from_files(ROOT, PILOT / "packet.json", when) for when in (BEFORE, AFTER))

    def test_replaying_the_saved_packet_reproduces_the_saved_map_and_recipe(self):
        for (event_map, recipe), folder in ((self.before, "2026-11-10"), (self.after, "2026-11-11")):
            saved = PILOT / "output" / folder
            self.assertEqual(event_map.model_dump_json(indent=1) + "\n", (saved / "event_map.json").read_text(encoding="utf-8"))
            self.assertEqual(recipe.model_dump_json(indent=1) + "\n", (saved / "recipe.json").read_text(encoding="utf-8"))

    def test_dated_source_appears_only_in_the_map_built_after_it_was_available(self):
        (early, early_recipe), (late, late_recipe) = self.before, self.after
        ids = lambda m: {e.evidence_id for e in m.evidence}
        self.assertNotIn(RELEASE, ids(early))
        self.assertIn(RELEASE, ids(late))
        self.assertLess(ids(early), ids(late))  # nothing is lost by waiting
        self.assertNotIn("586.89", early.model_dump_json() + early_recipe.model_dump_json())  # the figure itself is absent
        self.assertEqual(([o.session for o in early.content], [o.session for o in late.content]),
                         ([date(2026, 9, 30)], [date(2026, 9, 30), date(2026, 10, 31)]))
        # The source states a date and no time, so it counts from noon UTC the next day: after the earlier decision,
        # although that decision is itself after the filing time that bounds the event.
        self.assertEqual(early.event_time.basis, "upper_bound")
        self.assertLess(early.event_time.time, BEFORE)
        self.assertEqual(late.content[-1].available_at, datetime.fromisoformat("2026-11-11T12:00:00+00:00"))
        self.assertEqual((early_recipe.observability, late_recipe.observability), ("data_gap", "observable"))
        self.assertEqual({early.prior_status, late.prior_status, early_recipe.prior_status}, {"exploratory"})

    def test_edges_stay_hypotheses_and_a_gap_stays_visible(self):
        _, (late, recipe) = self.before, self.after
        edge = late.edges[0]
        self.assertEqual((edge.lead.relation_kind, edge.supports, edge.contradicts, edge.inconclusive, edge.gaps),
                         ("mechanism_hypothesis", 0, 0, 1, 1))
        self.assertTrue(all(e.relevance and e.lead.lead_id in {l.lead_id for l in recipe.leads} for e in late.edges))
        self.assertEqual([e.evidence_ids for e in late.edges[1:]], [(), ()])  # chosen as relevant, with nothing found yet
        self.assertIn("company:tsmc", late.nodes)
        self.assertEqual(self.before[0].edges[0].gaps, 1)  # the gap is there at the earlier time too

    def test_contract_and_generator_reject_what_a_decision_could_not_have(self):
        early, late = self.before[0], self.after[0]
        release = next(e for e in late.evidence if e.evidence_id == RELEASE)
        with self.assertRaisesRegex(ValueError, "strictly before the decision time"):
            EventMap.model_validate({**early.model_dump(), "evidence": [*early.evidence, release]})
        with self.assertRaisesRegex(ValueError, "strictly before the decision time"):
            EventMap.model_validate({**early.model_dump(), "content": list(late.content)})
        packet = json.loads((PILOT / "packet.json").read_text(encoding="utf-8"))
        packet["evidence"][2]["lead_id"] = "lead:not:a:chosen:path"
        with self.assertRaisesRegex(ValueError, "not a chosen path"):
            SourcePacket.model_validate(packet)

    def test_factor_runs_from_its_origin_lead_through_the_map_to_the_evaluator(self):
        catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text(encoding="utf-8"))
        b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
        periods = SessionCalendar(**json.loads((PILOT / "monthly_periods_2026.json").read_text(encoding="utf-8")))
        days = [date(2026, 11, 2) + timedelta(days=n) for n in range(19)]
        equity = SessionCalendar(calendar_id="test", timezone="America/New_York", first=days[0], last=days[-1],
                                 sessions=tuple(d for d in days if d.weekday() < 5), basis_note="Synthetic.")
        subject, benchmark = (b["yahoo:tsm:open"], b["yahoo:tsm:close"]), (b["yahoo:soxx:open"], b["yahoo:soxx:close"])
        prices = [observe(binding, s, 100.0) for s in equity.sessions for binding in (*subject, *benchmark)]  # synthetic, flat
        factor = Factor(factor_id="reported_revenue_growth_yoy", measure="level", series_ids=(SERIES,), max_age_days=45)
        rows = []
        for event_map, recipe in (self.before, self.after):
            event = EventTime(event_id=event_map.event_id,
                              cutoff=Cutoff(time=event_map.decision_time, basis="exact", source="Decision time"),
                              required_sessions={factor.factor_id: event_map.content_session})
            table = build_table(recipe, [event], [factor], {SERIES: b[SERIES]}, subject, benchmark,
                                list(event_map.content) + prices, equity, {}, {"synthetic": "0" * 64},
                                series_calendars={SERIES: periods})
            self.assertEqual(table.recipe_id, recipe.recipe_id)
            rows.append(table.rows[0])
        early, late = (r.factors[0] for r in rows)
        # Before the release is available the factor is a gap for October: September's figure does not stand in.
        self.assertEqual((early.state.status, early.state.session, early.value), ("gap", None, None))
        self.assertIn("2026-10-31 was not knowable", early.state.reason)
        self.assertEqual((late.state.status, late.state.session, late.value), ("requested", date(2026, 10, 31), 59.7))
        # The release states a date and no time, so its cutoff is a convention: usable, and not verified timing.
        self.assertEqual((self.after[0].content[-1].availability_basis, late.assumed_inputs, rows[1].timing_verified),
                         ("assumed", 1, False))
        self.assertEqual((self.after[0].content_series, self.after[0].content_session), (SERIES, date(2026, 10, 31)))
        without = build_table(self.before[1], [EventTime(event_id="e", cutoff=Cutoff(time=BEFORE, basis="exact", source="Decision time"))],
                              [factor], {SERIES: b[SERIES]}, subject, benchmark, list(self.before[0].content) + prices, equity, {},
                              {"synthetic": "0" * 64}, series_calendars={SERIES: periods}).rows[0].factors[0]
        self.assertEqual((without.state.status, without.value), ("fallback", 54.6))  # naming no session keeps the fallback

        def named(session, limit=45):  # ask for one period by name, with everything the later decision could see
            wanted = factor.model_copy(update={"max_age_days": limit})
            event = EventTime(event_id="e", cutoff=Cutoff(time=AFTER, basis="exact", source="Decision time"),
                              required_sessions={factor.factor_id: session})
            return build_table(self.after[1], [event], [wanted], {SERIES: b[SERIES]}, subject, benchmark,
                               list(self.after[0].content) + prices, equity, {}, {"synthetic": "0" * 64},
                               series_calendars={SERIES: periods}).rows[0].factors[0]

        september = named(date(2026, 9, 30))  # an older period is read directly, although a newer value exists
        self.assertEqual((september.state.status, september.state.session, september.value), ("requested", date(2026, 9, 30), 54.6))
        self.assertIn("over the limit", named(date(2026, 9, 30), limit=30).state.reason)  # the age limit applies to it
        self.assertIn("had not closed", named(date(2026, 11, 30)).state.reason)
        self.assertIn("not an expected session", named(date(2026, 10, 15)).state.reason)
        self.assertEqual([r.entry_session for r in rows], [date(2026, 11, 10), date(2026, 11, 11)])
        origin = self.after[1].leads[0]
        self.assertEqual((origin.lead_id, origin.to_id), ("lead:supply_chain:designers:orders:supply_chain:foundry", "company:tsmc"))

    def test_generator_refuses_a_packet_whose_bindings_have_changed(self):
        packet = SourcePacket.model_validate_json((PILOT / "packet.json").read_text(encoding="utf-8"))
        catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text(encoding="utf-8"))
        b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
        self.assertEqual(bindings_digest(packet, b), packet.bindings_sha256)
        self.assertEqual(self.after[0].inputs_sha256["bindings_used"], packet.bindings_sha256)
        edited = {**b, SERIES: b[SERIES].model_copy(update={"publication_delay_minutes": 1})}
        self.assertNotEqual(bindings_digest(packet, edited), packet.bindings_sha256)  # a used binding changed
        unrelated = {**b, "cboe:vix": b["cboe:vix"].model_copy(update={"publication_delay_minutes": 1})}
        self.assertEqual(bindings_digest(packet, unrelated), packet.bindings_sha256)  # an unused one did not matter
        with tempfile.TemporaryDirectory() as folder:
            stale = Path(folder) / "packet.json"
            stale.write_text(json.dumps({**json.loads((PILOT / "packet.json").read_text()), "bindings_sha256": "0" * 64}))
            with self.assertRaisesRegex(ValueError, "differs from the one it was pinned to"):
                build_from_files(ROOT, stale, AFTER)


if __name__ == "__main__":
    unittest.main()
