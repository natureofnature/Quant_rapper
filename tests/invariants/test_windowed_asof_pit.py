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
    from quant_pit_data.asof import observe, trailing_window
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

    def window(self, when, sessions, rows=None, limit=LIMIT):
        cutoff = Cutoff(time=when, basis="exact", source="Synthetic fixture.")
        return trailing_window(self.binding, self.rows if rows is None else rows, cutoff, self.calendar, sessions, limit)

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
