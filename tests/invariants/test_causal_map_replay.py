"""Invariant: a causal map is generated from a subject and a time alone, shows only what was available, and replays exactly.

Uses the tracked outputs: five subjects at one decision time, and TSMC's October 2026
revenue release at two decision times that bracket its dated source. Skipped when pydantic
is not installed.
"""

from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data", "quant_causal_graph", "quant_evaluator", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_causal_graph.causal_map import check_bindings, check_frozen_prior, generate_from_files
    from quant_contracts.causal_map import CausalMap, ResearchInputs, SourcePacket
    from quant_contracts.evaluation import EventTime
    from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
    from quant_loop.event_table import build_table
    from quant_loop.handoff import evaluate_candidate
    from quant_pit_data.asof import observe
except ModuleNotFoundError:
    CausalMap = None

MAPS = ROOT / "artifacts/causal_maps"
PILOT, CROSS = MAPS / "tsmc_monthly_revenue_2026-10", MAPS / "cross_subject_2026-11-22"
NEW_YORK = ZoneInfo("America/New_York")
BEFORE, AFTER = (datetime(2026, 11, day, 8, 0, tzinfo=NEW_YORK) for day in (10, 11))
COMMON = datetime(2026, 11, 22, 12, 0, tzinfo=NEW_YORK)
SUBJECTS = ("SOXX", "QQQ", "SPY", "TSM", "MU")
RELEASE = "ev:tsmc:monthly_revenue:2026-10:release"
GROWTH = "factor:reported_revenue_growth_yoy"
SERIES = "tsmc:monthly_revenue:yoy_pct_as_released"


def saved(folder: Path) -> tuple[str, str]:
    return tuple((folder / name).read_text(encoding="utf-8") for name in ("causal_map.json", "recipes.json"))


def dumped(result) -> tuple[str, str]:
    causal_map, recipes = result
    return (causal_map.model_dump_json(indent=1) + "\n",
            json.dumps([json.loads(r.model_dump_json()) for r in recipes], indent=1) + "\n")


@unittest.skipIf(CausalMap is None, "pydantic is not installed")
class CausalMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The saved maps record the factor definitions they were built from. A prior committed later was not available at
        # their decision times, so they are replayed from the last revision that still held those definitions, with today's code.
        definitions = "knowledge_base/prior_knowledge/bindings/factor_definitions.json"
        git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], check=True, capture_output=True).stdout
        recorded = json.loads((CROSS / "output/SOXX/causal_map.json").read_text(encoding="utf-8"))["inputs_sha256"]["factor_definitions"]
        touching = git("log", "--format=%H", "--", definitions).decode().split()
        held = next(i for i, c in enumerate(touching) if hashlib.sha256(git("show", f"{c}:{definitions}")).hexdigest() == recorded)
        cls.revision = "HEAD" if held == 0 else f"{touching[held - 1]}^"
        cls.checkout = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.checkout.cleanup)
        origin = Path(cls.checkout.name) / "repo"
        subprocess.run(["git", "clone", "-q", "--local", "--no-checkout", str(ROOT), str(origin)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(origin), "checkout", "-q", "--detach", git("rev-parse", cls.revision).decode().strip()], check=True, capture_output=True)
        packet = origin / PILOT.relative_to(ROOT) / "packet.json"
        cls.cross = {s: generate_from_files(origin, s, COMMON) for s in SUBJECTS}
        cls.before, cls.after = (generate_from_files(origin, "TSM", when, packet) for when in (BEFORE, AFTER))

    def test_a_prior_committed_after_the_decision_is_labelled_exploratory(self):
        inputs = ResearchInputs.model_validate_json((ROOT / "knowledge_base/prior_knowledge/bindings/factor_definitions.json").read_text(encoding="utf-8"))
        frozen = datetime.fromisoformat(subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cI", inputs.prior_commit],
                                                       check=True, capture_output=True, text=True).stdout.strip())
        for subject in SUBJECTS:  # the live prior, through the same entry point
            early, _ = generate_from_files(ROOT, subject, frozen - timedelta(seconds=1))
            late, _ = generate_from_files(ROOT, subject, frozen)
            self.assertEqual((early.prior_status, late.prior_status), ("exploratory", "prior_available_at_decision"), subject)
            self.assertEqual(late.inputs_sha256["prior_commit"], inputs.prior_commit)

    def test_replaying_the_same_inputs_reproduces_every_saved_map_and_recipe(self):
        for subject, result in self.cross.items():
            self.assertEqual(dumped(result), saved(CROSS / "output" / subject), subject)
        self.assertEqual(dumped(self.before), saved(PILOT / "output/2026-11-10"))
        self.assertEqual(dumped(self.after), saved(PILOT / "output/2026-11-11"))

    def test_prior_freeze_distinguishes_projection_inputs_from_display_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = ["git", "-C", temp]
            subprocess.run([*git, "init", "-q"], check=True)
            maps = root / "Relationships"
            projector = maps / "utils/research_map.py"
            profile = maps / "sector/ABC/company.json"
            display = maps / "relationship_visualizer.py"
            for path in (projector, profile, display):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original")
            subprocess.run([*git, "add", "Relationships"], check=True)
            subprocess.run([*git, "-c", "user.name=Test", "-c", "user.email=test@example.org",
                            "commit", "-qm", "freeze prior"], check=True)
            display.write_text("new layout")
            check_frozen_prior(maps, "HEAD")
            for path in (profile, projector):
                with self.subTest(path=path.name):
                    path.write_text("changed")
                    with self.assertRaisesRegex(ValueError, "research prior differs"):
                        check_frozen_prior(maps, "HEAD")
                    path.write_text("original")
            (profile.parent / "fund.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "research prior differs"):
                check_frozen_prior(maps, "HEAD")

    def test_five_subjects_go_through_one_entry_point_with_no_event_or_candidate_list(self):
        measurable = {}
        for subject, (causal_map, recipes) in self.cross.items():
            self.assertEqual((causal_map.event, causal_map.evidence, causal_map.prior_status), (None, (), "prior_available_at_decision"))
            leads = {lead.lead_id for lead in causal_map.leads}
            self.assertEqual({i for c in causal_map.candidates for i in c.origin_lead_ids}, leads)  # every lead is accounted for
            found = [c for c in causal_map.candidates if c.status == "measurable"]
            measurable[subject] = {c.factor.factor_id for c in found}
            self.assertEqual([r.recipe_id for r in recipes], [c.recipe_id for c in found])  # one recipe each, named by its candidate
            self.assertTrue(all(c.recipe_id is None for c in causal_map.candidates if c.status == "unmeasured"))
            for candidate in causal_map.candidates:
                self.assertTrue(candidate.relevance and (candidate.status == "measurable" or candidate.missing))
            trend = next(c for c in found if c.factor.factor_id == "trend_200")
            self.assertEqual((trend.reads, trend.factor.series_ids), ("proxy", (f"yahoo:{subject.lower()}:close",)))
        shared = {"vix_term_structure", "real_yield_10y_change_20_rank_250", "trend_200"}
        self.assertEqual(set(map(frozenset, measurable.values())), {frozenset(shared)})  # the shared market factors reach all five
        # ...but each by its own profile: a sector fund through a link into the shared map, a market fund directly.
        via = {s: next(c.via for c in self.cross[s][0].candidates if c.candidate_id == "factor:vix_term_structure") for s in SUBJECTS}
        self.assertEqual(via["SOXX"], ("concept:economy:risk_appetite", "concept:derivatives_flows:vol_term_structure"))
        self.assertEqual(via["QQQ"], ("concept:derivatives_flows:vol_term_structure",))
        counts = {s: (len(self.cross[s][0].leads), len(self.cross[s][0].candidates)) for s in SUBJECTS}
        self.assertEqual(len(set(counts.values())), 4)  # no fixed number: only TSM and MU, in the same roles, coincide
        self.assertEqual((self.cross["TSM"][1][0].benchmark, self.cross["SOXX"][1][0].benchmark), ("fund:soxx", None))
        tsm = {c.candidate_id: c for c in self.cross["TSM"][0].candidates}
        self.assertEqual(tsm[GROWTH].status, "unmeasured")  # defined for TSM, with no dated source on an ordinary day
        self.assertNotIn(GROWTH, {c.candidate_id for c in self.cross["MU"][0].candidates})  # and not offered to another company

    def test_dated_source_appears_only_in_the_map_built_after_it_was_available(self):
        (early, early_recipes), (late, late_recipes) = self.before, self.after
        ids = lambda m: {e.evidence_id for e in m.evidence}
        self.assertNotIn(RELEASE, ids(early))
        self.assertIn(RELEASE, ids(late))
        self.assertLess(ids(early), ids(late))  # nothing is lost by waiting
        self.assertNotIn("586.89", "".join(dumped(self.before)))  # the figure itself is absent
        # The source states a date and no time, so it counts from noon UTC the next day: after the earlier decision,
        # although that decision is itself after the filing time that bounds the event.
        self.assertEqual(early.event.event_time.basis, "upper_bound")
        self.assertLess(early.event.event_time.time, BEFORE)
        self.assertEqual((late.content[-1].available_at, late.content[-1].availability_basis),
                         (datetime.fromisoformat("2026-11-11T12:00:00+00:00"), "assumed"))
        growth = [next(c for c in m.candidates if c.candidate_id == GROWTH) for m in (early, late)]
        self.assertEqual([c.status for c in growth], ["unmeasured", "measurable"])
        self.assertIn("2026-10-31 was not available", growth[0].missing)
        self.assertEqual((len(early_recipes), len(late_recipes)), (3, 4))  # the gap in one candidate leaves the others
        self.assertEqual({early.prior_status, late.prior_status}, {"exploratory"})
        # The missing customer breakdown is visible at both times, on the candidate its lead feeds.
        self.assertEqual([(c.gaps, c.inconclusive, c.supports, c.contradicts) for c in growth], [(1, 0, 0, 0), (1, 1, 0, 0)])

    def test_contract_and_generator_reject_what_a_decision_could_not_have(self):
        early, late = self.before[0], self.after[0]
        release = next(e for e in late.evidence if e.evidence_id == RELEASE)
        with self.assertRaisesRegex(ValueError, "strictly before the decision time"):
            CausalMap.model_validate({**early.model_dump(), "evidence": [*early.evidence, release]})
        with self.assertRaisesRegex(ValueError, "strictly before the decision time"):
            CausalMap.model_validate({**early.model_dump(), "content": list(late.content)})
        with self.assertRaisesRegex(ValueError, "every lead must be the origin"):
            CausalMap.model_validate({**late.model_dump(), "candidates": late.candidates[:4]})
        with self.assertRaisesRegex(ValueError, "matches 0 profiles"):
            generate_from_files(ROOT, "NOT_A_SUBJECT", COMMON)

    def test_candidate_runs_from_its_origin_lead_through_the_map_to_the_evaluator(self):
        catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text(encoding="utf-8"))
        b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
        periods = SessionCalendar(**json.loads((PILOT / "monthly_periods_2026.json").read_text(encoding="utf-8")))
        days = [date(2026, 11, 2) + timedelta(days=n) for n in range(19)]
        equity = SessionCalendar(calendar_id="test", timezone="America/New_York", first=days[0], last=days[-1],
                                 sessions=tuple(d for d in days if d.weekday() < 5), basis_note="Synthetic.")
        subject, benchmark = (b["yahoo:tsm:open"], b["yahoo:tsm:close"]), (b["yahoo:soxx:open"], b["yahoo:soxx:close"])
        prices = [observe(binding, s, 100.0) for s in equity.sessions for binding in (*subject, *benchmark)]  # synthetic, flat
        late_map, late_recipes = self.after
        candidate = next(c for c in late_map.candidates if c.candidate_id == GROWTH)
        recipe, factor = next(r for r in late_recipes if r.recipe_id == candidate.recipe_id), candidate.factor
        self.assertEqual(recipe.leads[0].lead_id, "lead:supply_chain:designers:orders:supply_chain:foundry")
        self.assertLessEqual({SERIES, *(x.series_id for x in (*subject, *benchmark))}, {r.series for r in recipe.data_requirements})

        def row(causal_map, when, session, limit=45):
            event = EventTime(event_id="e", cutoff=Cutoff(time=when, basis="exact", source="Decision time"),
                              required_sessions={factor.factor_id: session} if session else {})
            return build_table(recipe, [event], [factor.model_copy(update={"max_age_days": limit})], {SERIES: b[SERIES]}, subject,
                               benchmark, list(causal_map.content) + prices, equity, {}, {"synthetic": "0" * 64},
                               series_calendars={SERIES: periods}).rows[0]

        october = late_map.event.content_session
        early, late = row(self.before[0], BEFORE, october), row(late_map, AFTER, october)
        # Before the release is available the factor is a gap for October: September's figure does not stand in.
        self.assertEqual((early.factors[0].state.status, early.factors[0].value), ("gap", None))
        self.assertIn("2026-10-31 was not knowable", early.factors[0].state.reason)
        self.assertEqual((late.factors[0].state.status, late.factors[0].state.session, late.factors[0].value),
                         ("requested", october, 59.7))
        # The release states a date and no time: usable, and not verified timing.
        self.assertEqual((late.factors[0].assumed_inputs, late.timing_verified), (1, False))
        self.assertEqual([early.entry_session, late.entry_session], [date(2026, 11, 10), date(2026, 11, 11)])
        # A factor that names no period keeps the ordinary fallback; a named older period is read directly.
        self.assertEqual((row(self.before[0], BEFORE, None).factors[0].state.status, row(self.before[0], BEFORE, None).factors[0].value),
                         ("fallback", 54.6))
        self.assertEqual(row(late_map, AFTER, date(2026, 9, 30)).factors[0].value, 54.6)
        self.assertIn("over the limit", row(late_map, AFTER, date(2026, 9, 30), limit=30).factors[0].state.reason)

    def test_handoff_keeps_factor_recipe_cutoff_and_bindings_together(self):
        catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text(encoding="utf-8"))
        b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
        periods = SessionCalendar(**json.loads((PILOT / "monthly_periods_2026.json").read_text(encoding="utf-8")))
        days = [date(2026, 11, 2) + timedelta(days=n) for n in range(60)]
        equity = SessionCalendar(calendar_id="test", timezone="America/New_York", first=days[0], last=days[-1],
                                 sessions=tuple(d for d in days if d.weekday() < 5), basis_note="Synthetic.")
        flat = lambda *symbols: [observe(b[f"yahoo:{s}:{k}"], d, 100.0) for d in equity.sessions for s in symbols for k in ("open", "close")]
        run = lambda causal_map, recipe, bindings=b: evaluate_candidate(
            causal_map, recipe, bindings, flat("tsm", "soxx"), equity, {}, {"synthetic": "0" * 64}, series_calendars={SERIES: periods})
        (early_map, early_recipes), (late_map, late_recipes) = self.before, self.after
        growth = next(c for c in late_map.candidates if c.candidate_id == GROWTH)
        recipe = next(r for r in late_recipes if r.recipe_id == growth.recipe_id)
        table = run(late_map, recipe)
        self.assertEqual((table.recipe_id, table.factors[0].factor_id, table.rows[0].factors[0].value),
                         (growth.recipe_id, "reported_revenue_growth_yoy", 59.7))
        # The earlier map has no recipe for the revenue candidate, and a recipe from the later cutoff is not accepted for it.
        self.assertIsNone(next(c for c in early_map.candidates if c.candidate_id == GROWTH).recipe_id)
        self.assertNotIn(growth.recipe_id, {r.recipe_id for r in early_recipes})
        with self.assertRaisesRegex(ValueError, "another decision time"):
            run(early_map, recipe)
        with self.assertRaisesRegex(ValueError, "another subject"):
            run(self.cross["MU"][0], recipe)
        # The same id in another context is another recipe: at one decision time, the ordinary-day map and the event map
        # both have a VIX recipe with this id. Their evidence differs, and each map accepts only its own.
        ordinary_map, ordinary_recipes = generate_from_files(ROOT, "TSM", AFTER)
        self.assertEqual(ordinary_recipes[0].recipe_id, late_recipes[0].recipe_id)
        self.assertNotEqual(ordinary_recipes[0].evidence, late_recipes[0].evidence)
        with self.assertRaisesRegex(ValueError, "same id, different content"):
            run(late_map, ordinary_recipes[0])
        with self.assertRaisesRegex(ValueError, "same id, different content"):
            run(ordinary_map, late_recipes[0])
        self.assertTrue(run(ordinary_map, ordinary_recipes[0]).rows[0].event_id.startswith("decision:company:tsmc:"))
        self.assertEqual(run(late_map, late_recipes[0]).recipe_sha256, late_map.candidates[0].recipe_sha256)
        # A recipe is evaluated with its own candidate's factor: the VIX recipe yields the VIX factor, never revenue growth.
        vix = run(early_map, early_recipes[0])
        self.assertEqual((vix.recipe_id.split(":")[3], vix.factors[0].factor_id), ("vix_term_structure", "vix_term_structure"))
        # Entry follows the declared trigger. A market state is triggered by the decision time, in an event map as on
        # an ordinary day, and its row is a decision, not the event. Only the event's own content waits for the release.
        timing = lambda m: {c.factor.factor_id: c.timing for c in m.candidates if c.factor is not None}
        self.assertEqual(timing(early_map), {"vix_term_structure": "decision_time", "real_yield_10y_change_20_rank_250": "decision_time",
                                             "trend_200": "decision_time", "reported_revenue_growth_yoy": "event_content"})
        self.assertEqual(timing(self.cross["TSM"][0])["reported_revenue_growth_yoy"], "decision_time")  # no event: the latest report
        self.assertTrue(all(c.timing is None for c in early_map.candidates if c.factor is None))
        self.assertEqual({r.trigger for r in early_recipes}, {"The decision time itself, on any US session"})
        self.assertNotIn("release", "".join(r.trigger for r in early_recipes))  # nothing claims to wait for content it lacks
        self.assertEqual((vix.rows[0].event_id, vix.rows[0].entry_session),
                         ("decision:company:tsmc:2026-11-10T08:00:00-05:00", date(2026, 11, 10)))
        self.assertIn("once its content is available", recipe.trigger)
        self.assertEqual((table.rows[0].event_id, table.rows[0].entry_session), ("tsmc:monthly_revenue:2026-10", date(2026, 11, 11)))
        # A binding that changed since the map was generated is refused, here the opening time that sets the entry session.
        moved = {**b, "yahoo:tsm:open": b["yahoo:tsm:open"].model_copy(update={"value_time": time(7, 0)})}
        with self.assertRaisesRegex(ValueError, "differ from the ones the map was generated with.*yahoo:tsm:open"):
            run(late_map, recipe, moved)
        # A fund's recipe names no benchmark, and its table holds the fund's own return with nothing subtracted.
        soxx_map, soxx_recipes = self.cross["SOXX"]
        absolute = evaluate_candidate(soxx_map, soxx_recipes[0], b, flat("soxx"), equity, {}, {"synthetic": "0" * 64})
        row = absolute.rows[0]
        self.assertEqual((soxx_recipes[0].benchmark, row.status, row.window.subject, row.window.benchmark, row.window.relative),
                         (None, "complete", 0.0, None, None))
        self.assertEqual((row.entry_session, row.exit_session), (date(2026, 11, 23), date(2026, 12, 18)))  # 20 sessions
        self.assertEqual(type(absolute).model_validate_json(absolute.model_dump_json()), absolute)  # and it loads back
        self.assertEqual(json.loads((PILOT / "output/2026-11-10/gap.json").read_text())["candidate_id"], GROWTH)
        self.assertFalse((PILOT / "output/2026-11-10/table.json").exists())

    def test_run_is_refused_when_a_binding_it_uses_is_unpinned_or_changed(self):
        catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text(encoding="utf-8"))
        b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
        pinned = ResearchInputs.model_validate_json(
            (ROOT / "knowledge_base/prior_knowledge/bindings/factor_definitions.json").read_text(encoding="utf-8")).bindings_sha256
        causal_map, recipes = self.after
        used = {r.series for recipe in recipes for r in recipe.data_requirements if r.series in b} | {SERIES}
        self.assertEqual({k: v for k, v in causal_map.inputs_sha256.items() if k.startswith("binding:")},
                         {f"binding:{i}": pinned[i] for i in used})
        edited = {**b, SERIES: b[SERIES].model_copy(update={"publication_delay_minutes": 1})}
        with self.assertRaisesRegex(ValueError, "have changed since.*yoy_pct_as_released"):
            check_bindings(used, edited, pinned)
        unrelated = {**b, "yahoo:spy:close": b["yahoo:spy:close"].model_copy(update={"publication_delay_minutes": 1})}
        self.assertEqual(check_bindings(used, unrelated, pinned), check_bindings(used, b, pinned))  # an unused one is free to change
        with self.assertRaisesRegex(ValueError, "not pinned"):
            check_bindings(used, b, {k: v for k, v in pinned.items() if k != "cboe:vix"})
        SourcePacket.model_validate_json((PILOT / "packet.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
