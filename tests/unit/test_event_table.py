"""The event table: session selection, return arithmetic and labels, on synthetic data.

Skipped when pydantic is not installed.
"""

from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data", "quant_causal_graph", "quant_evaluator", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_causal_graph.recipes import event_state_recipe, project_leads
    from quant_contracts.evaluation import EventTime, Factor
    from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
    from quant_evaluator.alpha.event_window import compare_groups, return_pair, simple_return
    from quant_evaluator.core.rolling import sessions_needed, windowed_value
    from quant_loop.daily_states import daily_states
    from quant_loop.event_table import SeriesIndex, build_table, factor_states
    from quant_pit_data.asof import latest_common, observe, on_calendar, trailing_window
except ModuleNotFoundError:
    SeriesBinding = None

RELATIONSHIPS = ROOT / "knowledge_base/prior_knowledge/Relationships"
NEW_YORK, TAIPEI = ZoneInfo("America/New_York"), ZoneInfo("Asia/Taipei")
FROZEN = datetime(2026, 11, 21, 13, 23, tzinfo=NEW_YORK)
RISK_LEAD = "lead:economy:risk_appetite:sector_beta:sector"
SESSIONS = tuple(date(2026, 11, d) for d in (5, 6, 9, 10, 11, 12, 13, 16, 17))


@unittest.skipIf(SeriesBinding is None, "pydantic is not installed")
class EventTableTests(unittest.TestCase):
    def setUp(self):
        def binding(series_id, value_time):
            return SeriesBinding(series_id=series_id, label=series_id, anchors=("test",),
                                 source_url="https://example.invalid", availability="official_archive",
                                 timezone="America/New_York", value_time=value_time,
                                 publication_delay_minutes=15, availability_basis="assumed",
                                 revisable=False, basis_note="Synthetic fixture.")

        self.state = (binding("state:near", "16:15"), binding("state:far", "16:15"))
        self.subject = (binding("subject:open", "09:30"), binding("subject:close", "16:00"))
        self.benchmark = (binding("benchmark:open", "09:30"), binding("benchmark:close", "16:00"))
        self.calendar = SessionCalendar(calendar_id="test", timezone="America/New_York", first=SESSIONS[0],
                                        last=SESSIONS[-1], sessions=SESSIONS, basis_note="Synthetic.")
        self.bindings = {b.series_id: b for b in self.state}
        self.factors = [Factor(factor_id="near_over_far", measure="ratio", series_ids=("state:near", "state:far"), max_age_days=5),
                        Factor(factor_id="near", measure="level", series_ids=("state:near",), max_age_days=5)]
        self.leads = project_leads(RELATIONSHIPS, "semiconductor", "TSM", FROZEN)
        self.recipe = event_state_recipe(
            "recipe:test", [self.leads[RISK_LEAD]], FROZEN + timedelta(hours=1), "Synthetic release", "filing",
            "SOXX", 5, [*self.state, *self.subject, *self.benchmark])
        # Subject opens at 100 + day and closes 1 higher; the benchmark is flat at 50.
        self.rows = [observe(binding, s, value) for s in SESSIONS for binding, value in (
            (self.state[0], 18.0), (self.state[1], 20.0), (self.subject[0], 100.0 + s.day),
            (self.subject[1], 101.0 + s.day), (self.benchmark[0], 50.0), (self.benchmark[1], 50.0))]

    def table(self, when, rows=None, dividends=None, recipe=None, basis="exact", state_before=None, factors=None):
        cutoff = Cutoff(time=when, basis=basis, source="Synthetic fixture.")
        state = state_before and Cutoff(time=state_before, basis="lower_bound", source="Synthetic fixture.")
        return build_table(recipe or self.recipe, [EventTime(event_id="event:1", cutoff=cutoff, state_cutoff=state)],
                           factors or self.factors, self.bindings, self.subject,
                           self.benchmark, self.rows if rows is None else rows, self.calendar, dividends or {},
                           {"synthetic": "0" * 64})

    def test_arithmetic(self):
        self.assertAlmostEqual(simple_return(100.0, 110.0), 0.10)
        pair = return_pair(100.0, 110.0, 50.0, 51.0)
        self.assertAlmostEqual(pair.relative, 0.08)
        self.assertIn("relative", pair.model_dump())  # the difference is part of the saved row
        with self.assertRaises(ValueError):
            simple_return(0.0, 10.0)

    def test_window_starts_at_the_first_open_after_the_cutoff_and_ends_on_the_fifth_session(self):
        row = self.table(datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)).rows[0]  # 00:30 in New York
        self.assertEqual((row.status, row.entry_session, row.exit_session), ("complete", date(2026, 11, 10), date(2026, 11, 16)))
        ratio, level = row.factors
        self.assertEqual((ratio.state.session, level.state.session), (date(2026, 11, 9), date(2026, 11, 9)))
        self.assertAlmostEqual(ratio.value, 0.9)
        self.assertAlmostEqual(level.value, 18.0)
        self.assertAlmostEqual(row.reaction.subject, 110.0 / 110.0 - 1)  # 9 Nov close 110 to 10 Nov open 110
        self.assertAlmostEqual(row.window.subject, 117.0 / 110.0 - 1)  # 10 Nov open to 16 Nov close
        self.assertAlmostEqual(row.window.relative, 117.0 / 110.0 - 1)

    def test_cutoff_after_the_open_enters_at_the_next_session(self):
        row = self.table(datetime(2026, 11, 10, 9, 30, tzinfo=NEW_YORK)).rows[0]  # at the open is not after it
        self.assertEqual(row.entry_session, date(2026, 11, 11))
        self.assertEqual(self.table(datetime(2026, 11, 10, 9, 29, tzinfo=NEW_YORK)).rows[0].entry_session, date(2026, 11, 10))

    def test_event_before_the_frozen_prior_is_exploratory_and_timing_is_reported_separately(self):
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        row = self.table(when, basis="upper_bound", state_before=when).rows[0]
        self.assertEqual((row.prior_status, row.timing_verified), ("exploratory", False))
        self.assertEqual(self.recipe.prior_status, "prior_available_at_decision")

    def test_factor_without_data_is_a_gap_on_its_own_and_the_row_keeps_its_returns(self):
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        table = self.table(when, rows=[r for r in self.rows if r.series_id != "state:far"])  # the far series does not exist yet
        ratio, level = table.rows[0].factors
        self.assertEqual((ratio.state.status, ratio.value), ("gap", None))
        self.assertLessEqual(len(ratio.state.skipped), 5)  # the search stops at the age limit, not at the start of history
        self.assertAlmostEqual(level.value, 18.0)
        self.assertEqual(table.rows[0].status, "complete")
        self.assertEqual(table.rows[0].window, self.table(when).rows[0].window)
        self.assertEqual((table.coverage("near_over_far"), table.coverage("near")), ((), ("event:1",)))
        with self.assertRaises(ValueError):
            Factor(factor_id="bad", measure="ratio", series_ids=("state:near",), max_age_days=5)

    def test_zero_denominator_leaves_one_factor_without_a_value_and_nothing_else_changes(self):
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        zero = [observe(self.state[1], r.session, 0.0) if r.series_id == "state:far" and r.session == date(2026, 11, 9) else r
                for r in self.rows]
        row = self.table(when, rows=zero).rows[0]
        ratio, level = row.factors
        self.assertEqual((ratio.value, ratio.state.status, len(ratio.state.observations)), (None, "resolved", 2))
        self.assertIn("state:far is zero on 2026-11-09", ratio.reason)
        self.assertAlmostEqual(level.value, 18.0)
        self.assertEqual((row.status, row.window), ("complete", self.table(when).rows[0].window))

    def test_timing_is_not_verified_when_no_factor_has_a_value(self):
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        sourced = [r.model_copy(update={"availability_basis": "sourced"}) for r in self.rows]
        self.assertTrue(self.table(when, rows=sourced).rows[0].timing_verified)  # exact cutoff, documented inputs
        prices_only = [r for r in sourced if not r.series_id.startswith("state:")]
        row = self.table(when, rows=prices_only).rows[0]
        self.assertEqual([s.state.status for s in row.factors], ["gap", "gap"])
        self.assertEqual((row.cutoff.basis, row.timing_verified, row.status), ("exact", False, "complete"))

    def test_cutoff_inside_a_session_is_flagged(self):
        inside = self.table(datetime(2026, 11, 10, 11, 0, tzinfo=NEW_YORK), basis="upper_bound",
                            state_before=datetime(2026, 11, 10, 0, 0, tzinfo=NEW_YORK)).rows[0]
        self.assertEqual((inside.entry_session, inside.flags), (date(2026, 11, 11), ("cutoff at or after the open of 2026-11-10",)))
        table = self.table(datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI))
        self.assertEqual(table.model_validate_json(table.model_dump_json()), table)  # a saved table loads back unchanged
        self.assertEqual(self.table(datetime(2026, 11, 8, 11, 0, tzinfo=NEW_YORK)).rows[0].flags, ())  # a Sunday

    def test_upper_bound_cutoff_reads_state_before_its_own_earlier_cutoff_and_enters_after_the_bound(self):
        accepted = datetime(2026, 11, 10, 17, 0, tzinfo=NEW_YORK)  # after that day's close was published
        row = self.table(accepted, basis="upper_bound", state_before=datetime(2026, 11, 10, 0, 0, tzinfo=NEW_YORK)).rows[0]
        self.assertEqual((row.factors[0].state.session, row.entry_session), (date(2026, 11, 9), date(2026, 11, 11)))
        self.assertEqual((row.cutoff.basis, row.state_cutoff.basis, row.timing_verified), ("upper_bound", "lower_bound", False))
        self.assertEqual(self.table(accepted).rows[0].factors[0].state.session, date(2026, 11, 10))  # an exact time may use it
        with self.assertRaisesRegex(ValueError, "upper bound"):
            self.table(accepted, basis="upper_bound")
        later = Cutoff(time=datetime(2026, 11, 12, 0, 0, tzinfo=NEW_YORK), basis="lower_bound", source="Synthetic fixture.")
        with self.assertRaisesRegex(ValueError, "cannot be after the cutoff"):  # a stored row gets the same check
            row.model_validate({**row.model_dump(), "state_cutoff": later})

    def test_measures_over_a_lookback(self):
        self.assertAlmostEqual(windowed_value("change", 2, None, [3.0, 9.0, 4.0]), 1.0)
        self.assertAlmostEqual(windowed_value("roc", 2, None, [4.0, 9.0, 5.0]), 0.25)
        self.assertAlmostEqual(windowed_value("trend_state", 3, None, [1.0, 2.0, 6.0]), 1.0)  # 6 over a mean of 3
        self.assertAlmostEqual(windowed_value("realized_vol", 2, None, [100.0, 110.0, 99.0]), 0.2 / 2 ** 0.5)
        # one-session changes are 1, 3, 2, -1, 2: the latest is above two of the four before it
        self.assertAlmostEqual(windowed_value("change", 1, 4, [0.0, 1.0, 4.0, 6.0, 5.0, 7.0]), 0.5)
        self.assertEqual(sessions_needed("change", 20, 250), 271)
        with self.assertRaisesRegex(ValueError, "not positive"):
            windowed_value("roc", 1, None, [0.0, 1.0])
        with self.assertRaises(ValueError):
            Factor(factor_id="bad", measure="roc", series_ids=("state:near",), max_age_days=5)  # no lookback

    def test_windowed_factor_reads_only_sessions_before_the_cutoff_and_is_not_shortened(self):
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        rising = [observe(self.state[0], r.session, float(r.session.day)) if r.series_id == "state:near" else r for r in self.rows]
        change = Factor(factor_id="near_change_2", measure="change", series_ids=("state:near",), max_age_days=5, lookback_sessions=2)
        state = self.table(when, rows=rising, factors=[change]).rows[0].factors[0]
        self.assertEqual((state.window_first, state.state.session, state.value), (date(2026, 11, 5), date(2026, 11, 9), 4.0))
        sourced = [r.model_copy(update={"availability_basis": "sourced"}) for r in rising]
        self.assertTrue(self.table(when, rows=sourced, factors=[change]).rows[0].timing_verified)
        mixed = [r.model_copy(update={"availability_basis": "assumed"})  # the window's first session, not its last
                 if (r.series_id, r.session) == ("state:near", date(2026, 11, 5)) else r for r in sourced]
        row = self.table(when, rows=mixed, factors=[change]).rows[0]
        self.assertEqual((row.factors[0].assumed_inputs, row.factors[0].state.verified, row.timing_verified), (1, True, False))
        with self.assertRaisesRegex(ValueError, "timing_verified does not follow"):
            row.model_validate({**row.model_dump(), "timing_verified": True})
        long = change.model_copy(update={"lookback_sessions": 3})  # would need a session before the calendar starts
        state = self.table(when, rows=rising, factors=[long]).rows[0].factors[0]
        self.assertEqual((state.value, state.state.status), (None, "resolved"))
        self.assertIn("fewer than 4", state.reason)

    def test_lookback_counts_the_series_own_sessions_when_its_market_is_closed_on_a_trading_day(self):
        closed = date(2026, 11, 11)  # a session for prices, a holiday for this series
        own = self.calendar.model_copy(update={"sessions": tuple(s for s in SESSIONS if s != closed), "calendar_id": "own"})
        rows = [observe(self.state[0], r.session, float(r.session.day)) if r.series_id == "state:near" else r
                for r in self.rows if not (r.series_id == "state:near" and r.session == closed)]
        change = Factor(factor_id="near_change_2", measure="change", series_ids=("state:near",), max_age_days=5, lookback_sessions=2)
        build = lambda calendars: build_table(
            self.recipe, [EventTime(event_id="event:1", cutoff=Cutoff(time=datetime(2026, 11, 13, 17, 0, tzinfo=NEW_YORK),
                                                                      basis="exact", source="Synthetic fixture."))],
            [change], self.bindings, self.subject, self.benchmark, rows, self.calendar, {}, {"synthetic": "0" * 64},
            series_calendars=calendars).rows[0].factors[0]
        shared = build(None)  # on the price calendar the closed day is a hole, so the window is incomplete
        self.assertEqual((shared.value, shared.state.session), (None, date(2026, 11, 13)))
        state = build({"state:near": own})  # on its own calendar the lookback is 13, 12, 10: each value once
        self.assertEqual((state.window_first, state.state.session, state.value), (date(2026, 11, 10), date(2026, 11, 13), 3.0))
        day_of = daily_states([closed], time(17, 0), [change], self.bindings, rows, self.calendar, {"state:near": own})
        cutoff, (held,) = day_of[0]  # on the closed day itself the previous value is carried, with its own date
        self.assertEqual((cutoff.basis, held.state.session, held.state.status, held.value),
                         ("exact", date(2026, 11, 10), "resolved", 4.0))

    def test_reading_recent_observations_gives_what_reading_all_history_gives(self):
        days = [date(2026, 1, 1) + timedelta(days=n) for n in range(300)]
        long = SessionCalendar(calendar_id="long", timezone="America/New_York", first=days[0], last=days[-1],
                               sessions=tuple(d for d in days if d.weekday() < 5), basis_note="Synthetic.")
        when = Cutoff(time=datetime(2026, 9, 30, 17, 0, tzinfo=NEW_YORK), basis="exact", source="Synthetic fixture.")
        late = datetime(2026, 10, 15, 9, 0, tzinfo=NEW_YORK)  # the last 60 sessions were only published after the cutoff
        recent = [s for s in long.sessions if s <= date(2026, 9, 30)][-60:]
        rows = [observe(b, s, float(n + k), late if s in recent else None)
                for n, s in enumerate(long.sessions) for k, b in enumerate(self.state)]
        factors = [Factor(factor_id="level", measure="level", series_ids=("state:near",), max_age_days=100),
                   Factor(factor_id="ratio", measure="ratio", series_ids=("state:near", "state:far"), max_age_days=100),
                   Factor(factor_id="change", measure="change", series_ids=("state:near",), max_age_days=100, lookback_sessions=30),
                   Factor(factor_id="short", measure="level", series_ids=("state:near",), max_age_days=5)]
        level, ratio, change, short = factor_states(factors, self.bindings, SeriesIndex(rows), when, long, {})
        limit = timedelta(days=100)
        self.assertEqual(level.state, latest_common([self.state[0]], rows, when, long, limit)[0])
        self.assertEqual(ratio.state, latest_common(list(self.state), rows, when, long, limit)[0])
        window = trailing_window(self.state[0], rows, when, long, 31, limit)
        self.assertEqual((change.state, change.window_first), (window.anchor, window.observations[0].session))
        self.assertEqual(short.state, latest_common([self.state[0]], rows, when, long, timedelta(days=5))[0])
        self.assertEqual((level.state.status, short.state.status), ("fallback", "gap"))  # found far back; out of reach at 5 days

    def test_group_comparison(self):
        result = compare_groups("x", [0.01, 0.03], [0.0, 0.0, 0.03])
        self.assertAlmostEqual(result.difference, 0.01)
        self.assertAlmostEqual(result.standard_error, (0.0002 / 2 + 0.0003 / 3) ** 0.5)
        self.assertEqual(compare_groups("x", [0.01], []).model_dump()["difference"], None)

    def test_ex_dividend_date_in_the_span_is_flagged(self):
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        inside = self.table(when, dividends={"ex-dividend TSM": [date(2026, 11, 10), date(2026, 11, 17)], "ex-dividend SOXX": [date(2026, 11, 9)]})
        self.assertEqual(inside.rows[0].flags, ("ex-dividend TSM 2026-11-10",))

    def test_window_running_past_the_data_is_incomplete_not_shortened(self):
        row = self.table(datetime(2026, 11, 12, 13, 30, tzinfo=TAIPEI)).rows[0]  # fifth session would be 18 Nov
        self.assertEqual((row.status, row.window), ("incomplete", None))
        missing = [r for r in self.rows if not (r.series_id == "benchmark:close" and r.session == date(2026, 11, 16))]
        row = self.table(datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI), rows=missing).rows[0]
        self.assertEqual(row.status, "incomplete")
        self.assertIn("benchmark:close on 2026-11-16", row.reason)

    def test_rows_on_days_that_are_not_sessions_are_dropped_on_purpose_not_silently(self):
        holiday = observe(self.state[0], date(2026, 11, 8), 19.0)  # a Sunday: no session in the calendar
        outside = observe(self.state[0], date(2026, 10, 1), 19.0)  # before the calendar starts: left alone
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        with self.assertRaisesRegex(ValueError, "not an expected session"):
            self.table(when, rows=self.rows + [holiday])
        kept, dropped = on_calendar(self.rows + [holiday, outside], self.calendar)
        self.assertEqual((dropped, len(kept)), ([holiday], len(self.rows) + 1))
        self.assertEqual(self.table(when, rows=kept).model_dump_json(), self.table(when).model_dump_json())

    def test_same_recipe_and_data_give_the_same_table(self):
        when = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)
        first, second = self.table(when), self.table(when, rows=list(reversed(self.rows)))
        self.assertEqual(first.model_dump_json(), second.model_dump_json())
        other = event_state_recipe("recipe:test", [self.leads[RISK_LEAD]], FROZEN + timedelta(hours=1),
                                   "Synthetic release", "filing", "SOXX", 3, [*self.state, *self.subject, *self.benchmark])
        self.assertNotEqual(first.recipe_sha256, self.table(when, recipe=other).recipe_sha256)

    def test_projected_leads_carry_the_frozen_revision_they_came_from(self):
        lead = self.leads[RISK_LEAD]
        self.assertEqual((lead.subject_id, lead.to_id, lead.hop, lead.scope), ("company:tsmc", "company:tsmc", "direct", "sector"))
        profile = json.loads((RELATIONSHIPS / "semiconductor/TSM/company.json").read_text())
        self.assertEqual(lead.source.sector_sha256, profile["sector"]["content_sha256"])
        self.assertEqual(lead.source.frozen_at, FROZEN)


if __name__ == "__main__":
    unittest.main()
