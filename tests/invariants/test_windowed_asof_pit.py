"""Invariant: a trailing window holds only values knowable before the cutoff, and is never shortened.

Synthetic data. Skipped when pydantic is not installed.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar, WindowedAsOf
    from quant_pit_data.asof import latest_common, observe, trailing_window
except ModuleNotFoundError:
    SeriesBinding = None

NEW_YORK = ZoneInfo("America/New_York")
HOLIDAY = date(2026, 11, 11)  # treated as a market holiday in this calendar
SESSIONS = tuple(date(2026, 11, d) for d in (5, 6, 9, 10, 12, 13, 16, 17))
LIMIT = timedelta(days=5)


@unittest.skipIf(SeriesBinding is None, "pydantic is not installed")
class WindowedAsOfTests(unittest.TestCase):
    def setUp(self):
        self.binding = SeriesBinding(series_id="x:close", label="x", anchors=("test",), source_url="https://example.invalid",
                                     availability="official_archive", timezone="America/New_York", value_time="16:00",
                                     publication_delay_minutes=15, availability_basis="sourced", revisable=False,
                                     basis_note="Synthetic fixture.")
        self.calendar = SessionCalendar(calendar_id="test", timezone="America/New_York", first=SESSIONS[0],
                                        last=SESSIONS[-1], sessions=SESSIONS, basis_note="Synthetic.")
        self.rows = [observe(self.binding, s, float(s.day)) for s in SESSIONS]

    def window(self, when, sessions, rows=None, limit=LIMIT, session=None):
        cutoff = Cutoff(time=when, basis="exact", source="Synthetic fixture.")
        return trailing_window(self.binding, self.rows if rows is None else rows, cutoff, self.calendar, sessions, limit, session)

    def test_window_ends_at_the_last_session_published_before_the_cutoff(self):
        before_publication = datetime(2026, 11, 13, 16, 10, tzinfo=NEW_YORK)  # closed at 16:00, published 16:15
        window = self.window(before_publication, 3)
        self.assertEqual([o.session.day for o in window.observations], [9, 10, 12])  # spans the holiday, excludes the 13th
        self.assertEqual((window.anchor.status, window.anchor.skipped[0].session), ("fallback", date(2026, 11, 13)))
        self.assertTrue(all(o.available_at < before_publication for o in window.observations))
        after = self.window(datetime(2026, 11, 13, 16, 16, tzinfo=NEW_YORK), 3)
        self.assertEqual(([o.session.day for o in after.observations], after.anchor.status), ([10, 12, 13], "resolved"))

    def test_window_with_a_missing_or_late_session_is_incomplete_not_shortened(self):
        when = datetime(2026, 11, 14, 9, 0, tzinfo=NEW_YORK)
        hole = self.window(when, 4, rows=[r for r in self.rows if r.session != date(2026, 11, 10)])
        self.assertEqual((hole.observations, hole.anchor.session), ((), date(2026, 11, 13)))
        self.assertIn("2026-11-10", hole.reason)
        late = datetime(2026, 11, 20, 9, 0, tzinfo=NEW_YORK)  # the 10th's value was only published after the cutoff
        rows = [observe(self.binding, r.session, r.value, late) if r.session == date(2026, 11, 10) else r for r in self.rows]
        self.assertEqual(self.window(when, 4, rows=rows).observations, ())
        self.assertEqual(len(self.window(when, 2, rows=rows).observations), 2)  # a window that does not reach it is whole
        self.assertIn("fewer than 9", self.window(when, 9).reason)  # the calendar itself is too short

    def test_window_can_end_at_a_named_session_under_the_same_cutoff_and_age_rules(self):
        when = datetime(2026, 11, 14, 9, 0, tzinfo=NEW_YORK)
        window = self.window(when, 2, session=date(2026, 11, 10))  # not the latest session: the 12th and 13th exist
        self.assertEqual(([o.session.day for o in window.observations], window.anchor.status), ([9, 10], "requested"))
        self.assertIn("over the limit", self.window(when, 2, session=date(2026, 11, 6)).anchor.reason)
        self.assertIn("had not closed", self.window(when, 2, session=date(2026, 11, 16)).anchor.reason)

    def test_revised_value_is_read_in_the_version_that_was_current_at_the_cutoff(self):
        day, when = date(2026, 11, 10), datetime(2026, 11, 14, 9, 0, tzinfo=NEW_YORK)
        first = next(r for r in self.rows if r.session == day)
        stamp = lambda d, h: datetime(2026, 11, d, h, 0, tzinfo=NEW_YORK)
        original = first.model_copy(update={"vintage_at": first.available_at})
        revised = first.model_copy(update={"value": 99.0, "vintage_at": stamp(13, 10)})  # same release stamp, revised later
        later = first.model_copy(update={"value": 77.0, "vintage_at": stamp(20, 10)})  # a revision from after the cutoff
        others = [r for r in self.rows if r.session != day]
        value = lambda rows, cut=when: {o.session: o.value for o in self.window(cut, 4, rows=rows).observations}[day]
        self.assertEqual(value(others + [original, revised, later]), 99.0)  # the latest version already public
        self.assertEqual(value(others + [later, revised, original]), 99.0)  # whatever order the rows come in
        self.assertEqual(value(others + [original, revised, later], stamp(13, 9)), 10.0)  # an hour before the revision: the original
        # A revision that is not yet available does not hide the earlier version, which is still usable.
        unreleased = first.model_copy(update={"value": 55.0, "vintage_at": stamp(13, 10), "available_at": stamp(20, 10)})
        self.assertEqual(value(others + [original, unreleased]), 10.0)
        self.assertEqual(len(self.window(when, 4, rows=others + [unreleased, original]).observations), 4)
        only_later = self.window(when, 4, rows=others + [later])
        self.assertEqual(only_later.observations, ())  # only a future version exists: the session has no value yet
        self.assertEqual(value(others + [first]), 10.0)  # a value with no vintage is used as before
        with self.assertRaisesRegex(ValueError, "duplicate observation"):
            self.window(when, 4, rows=others + [first, first.model_copy(update={"value": 5.0})])
        twin = original.model_copy(update={"value": 99.0})  # the same version stamp with another value
        for order in ([original, twin], [twin, original]):
            with self.assertRaisesRegex(ValueError, "duplicate observation"):
                self.window(when, 4, rows=others + order)  # refused either way round: order must not pick the value
        # A broken feed for another series in the same batch does not touch this one.
        other = self.binding.model_copy(update={"series_id": "y:close"})
        broken = [observe(other, day, 1.0), observe(other, day, 2.0)]  # two rows, no vintage: an error for y, nothing to do with x
        self.assertEqual(self.window(when, 4, rows=self.rows + broken), self.window(when, 4))
        resolved, _ = latest_common([self.binding], self.rows + broken, Cutoff(time=when, basis="exact", source="Synthetic fixture."), self.calendar, LIMIT)
        self.assertEqual(resolved.status, "resolved")
        with self.assertRaisesRegex(ValueError, "duplicate observation for y:close"):
            latest_common([other], self.rows + broken, Cutoff(time=when, basis="exact", source="Synthetic fixture."), self.calendar, LIMIT)
        # A saved result gets the same rule: a version from after the cutoff cannot be loaded back as if it had been known.
        good = self.window(when, 4, rows=others + [original, revised])
        moved = [o.model_copy(update={"vintage_at": stamp(20, 10)}) if o.session == day else o for o in good.observations]
        with self.assertRaisesRegex(ValueError, "in that version"):
            WindowedAsOf(anchor=good.anchor, sessions=4, observations=tuple(moved))
        late_anchor = good.anchor.observations[0].model_copy(update={"vintage_at": stamp(20, 10)})
        with self.assertRaisesRegex(ValueError, "in that version"):
            type(good.anchor).model_validate({**good.anchor.model_dump(), "observations": [late_anchor]})

    def test_stale_series_is_a_gap_not_an_old_window(self):
        old = [r for r in self.rows if r.session <= date(2026, 11, 9)]
        window = self.window(datetime(2026, 11, 17, 17, 0, tzinfo=NEW_YORK), 2, rows=old)
        self.assertEqual((window.anchor.status, window.observations, window.reason), ("gap", (), None))
        self.assertIn("over the limit", window.anchor.reason)

    def test_contract_rejects_a_window_holding_a_value_from_after_the_cutoff(self):
        good = self.window(datetime(2026, 11, 14, 9, 0, tzinfo=NEW_YORK), 3)
        leaked = good.observations[0].model_copy(update={"available_at": datetime(2026, 11, 15, 9, 0, tzinfo=NEW_YORK)})
        with self.assertRaisesRegex(ValueError, "strictly before the cutoff"):
            WindowedAsOf(anchor=good.anchor, sessions=3, observations=(leaked, *good.observations[1:]))
        with self.assertRaisesRegex(ValueError, "requested sessions"):
            WindowedAsOf(anchor=good.anchor, sessions=3, observations=good.observations[1:])


if __name__ == "__main__":
    unittest.main()
