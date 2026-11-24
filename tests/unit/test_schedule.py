"""A count of sessions to the next scheduled event uses the schedule as it stood on the day of the count."""

from datetime import date, timedelta
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_contracts.pit import SessionCalendar
    from quant_pit_data.schedule import scheduled_as_known, sessions_to_next
except ModuleNotFoundError:
    SessionCalendar = None


@unittest.skipIf(SessionCalendar is None, "pydantic is not installed")
class ScheduleTests(unittest.TestCase):
    def setUp(self):
        days = [date(2020, 1, 27) + timedelta(days=n) for n in range(100)]
        self.calendar = SessionCalendar(calendar_id="test", timezone="America/New_York", first=days[0], last=days[-1],
                                        sessions=tuple(d for d in days if d.weekday() < 5), basis_note="Weekdays only; synthetic.")
        self.held = [date(2020, 1, 29), date(2020, 4, 29)]  # the meetings that produced a statement: hindsight
        recorded = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/event_schedules.json").read_text(encoding="utf-8"))["fomc"]["withdrawn"]
        self.withdrawn = [(date.fromisoformat(w["scheduled"]), date.fromisoformat(w["known_on"])) for w in recorded]

    def count(self, session):
        return sessions_to_next(self.calendar, session, scheduled_as_known(self.held, self.withdrawn, session))

    def test_a_withdrawn_meeting_is_counted_until_the_day_its_withdrawal_was_known(self):
        self.assertEqual(self.withdrawn, [(date(2020, 3, 18), date(2020, 3, 15))])
        self.assertEqual(self.count(date(2020, 3, 13)), 3)  # Friday: Monday, Tuesday, then the meeting on Wednesday the 18th
        self.assertEqual(sessions_to_next(self.calendar, date(2020, 3, 13), self.held), 33)  # what the list of held meetings alone gives
        self.assertEqual(self.count(date(2020, 3, 16)), 32)  # the first session after the Sunday announcement: April is next
        self.assertEqual(self.count(date(2020, 1, 30)), 34)  # the day after January's meeting already counts to March

    def test_the_meeting_day_is_zero_and_nothing_ahead_or_a_non_session_gives_no_count(self):
        self.assertEqual((self.count(date(2020, 1, 29)), self.count(date(2020, 1, 28))), (0, 1))
        self.assertIsNone(self.count(date(2020, 4, 30)))
        self.assertIsNone(self.count(date(2020, 3, 14)))  # a Saturday

    def test_a_meeting_after_the_calendar_ends_gives_no_count(self):
        days = [date(2026, 12, 1) + timedelta(days=n) for n in range(31)]
        year = SessionCalendar(calendar_id="test", timezone="America/New_York", first=days[0], last=days[-1],
                               sessions=tuple(d for d in days if d.weekday() < 5), basis_note="Weekdays only; synthetic.")
        meetings = [date(2026, 12, 9), date(2027, 1, 27)]
        self.assertEqual(sessions_to_next(year, date(2026, 12, 8), meetings), 1)  # the December meeting is inside the calendar
        for session in (date(2026, 12, 10), date(2026, 12, 31)):  # January's is not: the sessions to it cannot be counted
            self.assertIsNone(sessions_to_next(year, session, meetings), session)
        self.assertEqual(sessions_to_next(year, date(2026, 12, 30), [date(2026, 12, 31)]), 1)  # the last day itself is covered


if __name__ == "__main__":
    unittest.main()
