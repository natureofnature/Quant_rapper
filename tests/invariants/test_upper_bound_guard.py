"""Invariant: an upper bound on an event's time never selects the state "before" the event.

Skipped when pydantic is not installed.
"""

from datetime import datetime
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/quant_contracts/src"))
try:
    from quant_contracts.evaluation import EventTime
    from quant_contracts.pit import Cutoff
except ModuleNotFoundError:
    EventTime = None

NEW_YORK = ZoneInfo("America/New_York")
ACCEPTED = datetime(2026, 11, 10, 7, 1, tzinfo=NEW_YORK)  # e.g. an SEC filing acceptance time
MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=NEW_YORK)


def at(when, basis):
    return Cutoff(time=when, basis=basis, source="Synthetic fixture.")


@unittest.skipIf(EventTime is None, "pydantic is not installed")
class UpperBoundGuardTests(unittest.TestCase):
    def test_upper_bound_alone_is_rejected_for_pre_event_state(self):
        with self.assertRaisesRegex(ValueError, "cannot be read before an upper bound"):
            EventTime(event_id="e", cutoff=at(ACCEPTED, "upper_bound"))
        with self.assertRaisesRegex(ValueError, "cannot be read before an upper bound"):
            EventTime(event_id="e", cutoff=at(ACCEPTED, "upper_bound"), state_cutoff=at(MIDNIGHT, "upper_bound"))

    def test_upper_bound_with_an_earlier_state_cutoff_is_accepted(self):
        for basis in ("lower_bound", "assumed", "exact"):
            event = EventTime(event_id="e", cutoff=at(ACCEPTED, "upper_bound"), state_cutoff=at(MIDNIGHT, basis))
            self.assertEqual((event.state.time, event.cutoff.time), (MIDNIGHT, ACCEPTED))
        for basis in ("exact", "assumed"):  # one known or assumed time serves both questions
            self.assertEqual(EventTime(event_id="e", cutoff=at(ACCEPTED, basis)).state.time, ACCEPTED)

    def test_bounds_pointing_the_unsafe_way_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot follow a lower bound"):
            EventTime(event_id="e", cutoff=at(ACCEPTED, "lower_bound"))
        with self.assertRaisesRegex(ValueError, "cannot be after the cutoff"):
            EventTime(event_id="e", cutoff=at(MIDNIGHT, "exact"), state_cutoff=at(ACCEPTED, "lower_bound"))


if __name__ == "__main__":
    unittest.main()
