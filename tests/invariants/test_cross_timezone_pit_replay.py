"""Invariant: a decision input must have been knowable before the cutoff it conditions.

Synthetic cases plus one real event from a tracked fixture. Skipped when pydantic is not installed.
"""

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_contracts.pit import AsOfResult, Cutoff, EventBinding, SeriesBinding, SessionCalendar
    from quant_pit_data.asof import latest_common, observe
except ModuleNotFoundError:
    SeriesBinding = None

BINDINGS = ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json"
FIXTURE = ROOT / "tests/fixtures/tsmc_2026_10_revenue_asof.json"
TAIPEI, NEW_YORK = ZoneInfo("Asia/Taipei"), ZoneInfo("America/New_York")
LIMIT = timedelta(days=5)
NOV = (date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10))
RELEASE = datetime(2026, 11, 10, 13, 30, tzinfo=TAIPEI)  # 00:30 in New York the same date


def weekdays(first: date, last: date, holidays=()) -> tuple[date, ...]:
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return tuple(d for d in days if d.weekday() < 5 and d not in holidays)


@unittest.skipIf(SeriesBinding is None, "pydantic is not installed")
class CrossTimezoneReplayTests(unittest.TestCase):
    def setUp(self):
        data = json.loads(BINDINGS.read_text(encoding="utf-8"))
        self.series = {s["series_id"]: SeriesBinding(**s) for s in data["series"]}
        self.vix, self.vix3m = self.series["cboe:vix"], self.series["cboe:vix3m"]
        self.events = [EventBinding(**e) for e in data["events"]]
        self.pilot_calendar = SessionCalendar(**json.loads(FIXTURE.read_text())["calendar"])
        self.both = [self.vix, self.vix3m]

    def closes(self, *sessions, skip=()):
        """One value per series per session; ``skip`` holds (series_id, session) pairs to leave out."""
        return [observe(b, s, 20.0 + s.day) for s in sessions for b in self.both
                if (b.series_id, s) not in skip]

    def resolve(self, observations, when, limit=LIMIT, basis="exact", holidays=(), calendar=None):
        calendar = calendar or SessionCalendar(
            calendar_id="test", timezone="America/New_York", basis_note="Synthetic.",
            first=when.date() - timedelta(days=30), last=when.date() + timedelta(days=5),
            sessions=weekdays(when.date() - timedelta(days=30), when.date() + timedelta(days=5), holidays))
        cutoff = Cutoff(time=when, basis=basis, source="Synthetic fixture.")
        return latest_common(self.both, observations, cutoff, calendar, limit)

    def test_taiwan_release_before_the_us_open_uses_the_previous_us_close(self):
        result, audit = self.resolve(self.closes(*NOV), RELEASE)
        self.assertEqual((result.status, result.session), ("resolved", date(2026, 11, 9)))
        self.assertTrue(all(o.available_at < result.cutoff.time for o in result.observations))
        self.assertEqual((result.skipped, audit), ((), ()))  # the same-day close is not expected yet

    def test_same_calendar_date_close_is_never_leaked(self):
        for when in (RELEASE, datetime(2026, 11, 10, 7, 1, 33, tzinfo=NEW_YORK)):
            self.assertNotEqual(self.resolve(self.closes(*NOV), when)[0].session, date(2026, 11, 10))

    def test_value_is_usable_only_after_its_publication_delay(self):
        data = self.closes(*NOV)
        before, _ = self.resolve(data, datetime(2026, 11, 10, 16, 29, tzinfo=NEW_YORK))
        exact, _ = self.resolve(data, datetime(2026, 11, 10, 16, 30, tzinfo=NEW_YORK))
        after, _ = self.resolve(data, datetime(2026, 11, 10, 16, 31, tzinfo=NEW_YORK))
        self.assertEqual((before.status, before.session), ("fallback", date(2026, 11, 9)))
        self.assertEqual(exact.session, date(2026, 11, 9))  # available_at must be strictly before the cutoff
        self.assertEqual((after.status, after.session), ("resolved", date(2026, 11, 10)))

    def test_monday_release_reaches_back_over_the_weekend(self):
        data = self.closes(date(2026, 8, 6), date(2026, 8, 7))
        result, _ = self.resolve(data, datetime(2026, 8, 10, 13, 30, tzinfo=TAIPEI))
        self.assertEqual((result.status, result.session), ("resolved", date(2026, 8, 7)))

    def test_holiday_is_not_expected_so_it_is_not_a_skip(self):
        thanksgiving = date(2026, 11, 26)
        data = self.closes(date(2026, 11, 24), date(2026, 11, 25))
        result, _ = self.resolve(data, datetime(2026, 11, 27, 13, 30, tzinfo=TAIPEI), holidays=(thanksgiving,))
        self.assertEqual((result.status, result.session, result.skipped), ("resolved", date(2026, 11, 25), ()))
        with self.assertRaisesRegex(ValueError, "not an expected session"):  # a row on the holiday is a data error
            self.resolve(data + self.closes(thanksgiving), datetime(2026, 11, 27, 13, 30, tzinfo=TAIPEI),
                         holidays=(thanksgiving,))

    def test_session_missing_from_every_series_is_a_fallback_not_a_holiday(self):
        result, audit = self.resolve(self.closes(date(2026, 11, 6), date(2026, 11, 10)), RELEASE)
        self.assertEqual((result.status, result.session), ("fallback", date(2026, 11, 6)))
        self.assertEqual([(s.session, s.unavailable) for s in result.skipped],
                         [(date(2026, 11, 9), ("cboe:vix", "cboe:vix3m"))])
        self.assertEqual({n.detail for n in audit}, {"no row in the data"})

    def test_session_missing_in_one_series_is_an_explicit_fallback(self):
        data = self.closes(*NOV, skip={("cboe:vix3m", date(2026, 11, 9))})
        result, _ = self.resolve(data, RELEASE)
        self.assertEqual((result.status, result.session), ("fallback", date(2026, 11, 6)))
        self.assertEqual([(s.session, s.unavailable) for s in result.skipped], [(date(2026, 11, 9), ("cboe:vix3m",))])
        self.assertEqual({o.session for o in result.observations}, {date(2026, 11, 6)})

    def test_late_publication_time_stays_out_of_the_decision_facing_result(self):
        late = datetime(2026, 11, 10, 9, 0, tzinfo=NEW_YORK)  # published the next morning
        data = self.closes(date(2026, 11, 6)) + [observe(self.vix, date(2026, 11, 9), 29.0),
                                                 observe(self.vix3m, date(2026, 11, 9), 27.0, available_at=late)]
        result, audit = self.resolve(data, RELEASE)
        self.assertEqual((result.status, result.session), ("fallback", date(2026, 11, 6)))
        self.assertEqual(result.skipped[0].unavailable, ("cboe:vix3m",))
        facing = result.model_dump_json()
        self.assertNotIn("2026-11-10T09:00", facing)  # when it finally appeared is later knowledge
        self.assertNotIn("27.0", facing)
        self.assertEqual([(n.series_id, n.detail) for n in audit],
                         [("cboe:vix3m", "published at 2026-11-10T09:00:00-05:00")])

    def test_duplicate_rows_are_rejected_whatever_their_order(self):
        extra = observe(self.vix, date(2026, 11, 9), 99.0, available_at=datetime(2026, 11, 10, 9, 0, tzinfo=NEW_YORK))
        data = self.closes(*NOV)
        for rows in (data + [extra], [extra] + data):
            with self.assertRaisesRegex(ValueError, "duplicate observation for cboe:vix on 2026-11-09"):
                self.resolve(rows, RELEASE)

    def test_stale_or_absent_data_is_a_gap_not_a_value(self):
        old, _ = self.resolve(self.closes(date(2026, 10, 30)), RELEASE)
        self.assertEqual((old.status, old.session, old.observations), ("gap", None, ()))
        self.assertIn("over the limit", old.reason)
        self.assertEqual(self.resolve([], RELEASE)[0].status, "gap")
        self.assertEqual(self.resolve([observe(self.vix, date(2026, 11, 9), 29.0)], RELEASE)[0].status, "gap")

    def test_cutoff_must_be_aware_and_inside_the_calendar(self):
        with self.assertRaises(ValueError):
            Cutoff(time=datetime(2026, 11, 10, 13, 30), basis="exact", source="Synthetic fixture.")
        with self.assertRaisesRegex(ValueError, "does not cover the cutoff"):
            self.resolve(self.closes(*NOV), datetime(2026, 12, 10, 13, 30, tzinfo=TAIPEI), calendar=self.pilot_calendar)

    def test_resolved_is_verified_only_with_an_exact_cutoff_and_no_assumed_times(self):
        recorded = [observe(b, s, 20.0, available_at=datetime.combine(s, time(17), NEW_YORK))
                    for s in NOV[:2] for b in self.both]
        self.assertTrue(self.resolve(recorded, RELEASE)[0].verified)
        self.assertFalse(self.resolve(recorded, RELEASE, basis="upper_bound")[0].verified)
        self.assertFalse(self.resolve(self.closes(*NOV), RELEASE)[0].verified)  # the binding's delay is assumed

    def test_fallback_preserves_recorded_values_but_is_unverified(self):
        old = [observe(b, NOV[0], 20.0, available_at=datetime.combine(NOV[0], time(17), NEW_YORK))
               for b in self.both]
        result, _ = self.resolve(old + self.closes(NOV[1]), datetime.combine(NOV[1], time(16, 20), NEW_YORK))
        self.assertEqual((result.status, result.session, result.observations), ("fallback", NOV[0], tuple(old)))
        self.assertEqual(result.skipped[0].session, NOV[1])
        self.assertFalse(result.verified)

    def test_non_gap_result_requires_one_observation_per_series_for_its_session(self):
        rows = self.closes(NOV[1])
        cutoff = Cutoff(time=RELEASE, basis="exact", source="Synthetic fixture.")
        for status in ("resolved", "fallback"):
            for session, observations in ((None, rows), (NOV[1], []), (NOV[1], rows[:1]),
                                          (NOV[1], rows + rows[:1]), (NOV[0], rows)):
                with self.subTest(status=status, session=session, observations=len(observations)):
                    with self.assertRaises(ValueError):
                        AsOfResult(cutoff=cutoff, series_ids=(self.vix.series_id, self.vix3m.series_id),
                                   status=status, session=session, observations=observations)

    def test_result_cannot_hold_a_value_from_at_or_after_its_cutoff(self):
        rows = self.closes(NOV[1])  # available at 16:30 New York on 9 November
        ids = (self.vix.series_id, self.vix3m.series_id)
        for minute, accepted in ((29, False), (30, False), (31, True)):
            cutoff = Cutoff(time=datetime(2026, 11, 9, 16, minute, tzinfo=NEW_YORK), basis="exact", source="Synthetic fixture.")
            with self.subTest(minute=minute):
                if accepted:
                    AsOfResult(cutoff=cutoff, series_ids=ids, status="resolved", session=NOV[1], observations=rows)
                else:
                    with self.assertRaisesRegex(ValueError, "strictly before the cutoff"):
                        AsOfResult(cutoff=cutoff, series_ids=ids, status="resolved", session=NOV[1], observations=rows)

    def test_gap_result_has_a_reason_and_no_session_or_values(self):
        rows = self.closes(NOV[1])
        cutoff = Cutoff(time=RELEASE, basis="exact", source="Synthetic fixture.")
        ids = (self.vix.series_id, self.vix3m.series_id)
        AsOfResult(cutoff=cutoff, series_ids=ids, status="gap", reason="Nothing usable.")
        for fields in ({}, {"reason": "x", "session": NOV[1]}, {"reason": "x", "observations": rows},
                       {"reason": "x", "session": NOV[1], "observations": rows}):
            with self.subTest(fields=sorted(fields)):
                with self.assertRaises(ValueError):
                    AsOfResult(cutoff=cutoff, series_ids=ids, status="gap", **fields)
        with self.assertRaisesRegex(ValueError, "only for a gap"):
            AsOfResult(cutoff=cutoff, series_ids=ids, status="resolved", session=NOV[1], observations=rows, reason="x")

    def test_close_time_follows_daylight_saving(self):
        summer = observe(self.vix, date(2026, 7, 1), 20.0).available_at.astimezone(timezone.utc)
        winter = observe(self.vix, date(2026, 12, 1), 20.0).available_at.astimezone(timezone.utc)
        self.assertEqual((summer.time(), winter.time()), (time(20, 30), time(21, 30)))

    def test_real_event_resolves_under_both_recorded_upper_bounds(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        rows = [observe(self.series[series], date.fromisoformat(day), value)
                for series, values in fixture["rows"].items() for day, value in values.items()]
        expected = fixture["expected"]
        for cutoff in (Cutoff(**c) for c in fixture["cutoffs"]):
            result, audit = latest_common(self.both, rows, cutoff, self.pilot_calendar, LIMIT)
            values = {o.series_id: o.value for o in result.observations}
            self.assertEqual((result.status, str(result.session), result.verified),
                             (expected["status"], expected["session"], expected["verified"]))
            self.assertAlmostEqual(values["cboe:vix"] / values["cboe:vix3m"], expected["ratio"], places=4)
            self.assertEqual({o.available_at for o in result.observations},
                             {datetime.fromisoformat(expected["available_at"])})
            self.assertEqual((result.cutoff.basis, audit), ("upper_bound", ()))
            price, _ = latest_common([self.series["yahoo:soxx:close"]], rows, cutoff, self.pilot_calendar, LIMIT)
            self.assertEqual((str(price.session), price.observations[0].value, price.verified),
                             (expected["session"], expected["soxx_close"], expected["verified"]))

    def test_bindings_anchor_to_ids_that_exist_in_the_research_maps(self):
        known = set()
        for path in (ROOT / "knowledge_base/prior_knowledge/Relationships").glob("*/*.json"):
            known.update(c["id"] for c in json.loads(path.read_text(encoding="utf-8")).get("concepts", []))
        for path in (ROOT / "knowledge_base/prior_knowledge/Relationships").glob("*/*/*.json"):
            profile = json.loads(path.read_text(encoding="utf-8"))
            known.update(v for k, v in profile.items() if k in ("company_id", "fund_id"))
        for binding in (*self.series.values(), *self.events):
            self.assertLessEqual(set(binding.anchors), known, binding)


if __name__ == "__main__":
    unittest.main()
