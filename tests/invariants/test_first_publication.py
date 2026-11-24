"""Invariant: a value dated before its series was first published is never selected, whatever the source file holds.

An exchange can publish a new index with a history calculated for earlier dates. Nobody
had those values then, so neither a decision nor a training row may read them. Synthetic
data. Skipped when pydantic is not installed.
"""

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data", "quant_causal_graph"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_causal_graph.causal_map import binding_digest
    from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
    from quant_pit_data.asof import at_session, latest_common, load_daily_closes, observe, ratio_series, trailing_window
    from quant_pit_data.connectors.yahoo_chart import load_closes, load_daily
except ModuleNotFoundError:
    SeriesBinding = None

NEW_YORK = ZoneInfo("America/New_York")
SESSIONS = tuple(date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17, 18))
LAUNCH = date(2026, 11, 12)  # the file below also holds the 9th, 10th and 11th, calculated afterwards
LIMIT = timedelta(days=5)


@unittest.skipIf(SeriesBinding is None, "pydantic is not installed")
class FirstPublicationTests(unittest.TestCase):
    def setUp(self):
        fields = dict(series_id="x:index", label="x", anchors=("test",), source_url="https://example.invalid",
                      availability="official_archive", timezone="America/New_York", value_time="16:00", publication_delay_minutes=15,
                      availability_basis="sourced", revisable=False, basis_note="Synthetic fixture.")
        self.undated, self.dated = SeriesBinding(**fields), SeriesBinding(**fields, first_published=LAUNCH)
        self.calendar = SessionCalendar(calendar_id="test", timezone="America/New_York", first=SESSIONS[0], last=SESSIONS[-1],
                                        sessions=SESSIONS, basis_note="Synthetic.")
        self.rows = [observe(self.undated, s, float(s.day)) for s in SESSIONS]  # the downloaded file: every session has a value

    def cutoff(self, day, hour=17):
        return Cutoff(time=datetime(2026, 11, day, hour, 0, tzinfo=NEW_YORK), basis="exact", source="Synthetic fixture.")

    def test_a_value_dated_before_the_launch_is_a_gap_and_the_launch_day_itself_is_usable(self):
        day_before, launch_day = self.cutoff(11), self.cutoff(12)
        with_history, _ = latest_common([self.undated], self.rows, day_before, self.calendar, LIMIT)
        self.assertEqual((with_history.status, with_history.session), ("resolved", date(2026, 11, 11)))  # without a date the backfill is read
        early, audit = latest_common([self.dated], self.rows, day_before, self.calendar, LIMIT)
        self.assertEqual((early.status, early.observations), ("gap", ()))
        self.assertTrue(all(note.detail == "no row in the data" for note in audit))
        first, _ = latest_common([self.dated], self.rows, launch_day, self.calendar, LIMIT)
        self.assertEqual((first.status, first.session, first.observations[0].value), ("resolved", LAUNCH, 12.0))
        self.assertEqual(at_session([self.dated], self.rows, launch_day, self.calendar, date(2026, 11, 11), LIMIT).status, "gap")
        self.assertEqual(at_session([self.dated], self.rows, launch_day, self.calendar, LAUNCH, LIMIT).status, "requested")

    def test_a_window_reaching_before_the_launch_is_incomplete_until_enough_real_sessions_exist(self):
        window = lambda binding, day, n: trailing_window(binding, self.rows, self.cutoff(day), self.calendar, n, LIMIT)
        self.assertEqual(len(window(self.undated, 16, 4).observations), 4)  # the backfill would have filled it
        short = window(self.dated, 16, 4)  # 11, 12, 13, 16: the 11th is before the launch
        self.assertEqual((short.observations, short.anchor.session), ((), date(2026, 11, 16)))
        self.assertIn("2026-11-11", short.reason)
        whole = window(self.dated, 17, 4)  # 12, 13, 16, 17: the first window made only of published values
        self.assertEqual([o.session.day for o in whole.observations], [12, 13, 16, 17])

    def test_one_dated_series_does_not_hide_another_series_values(self):
        other = self.undated.model_copy(update={"series_id": "y:index"})
        rows = self.rows + [observe(other, s, 1.0) for s in SESSIONS]
        both, _ = latest_common([self.dated, other], rows, self.cutoff(11), self.calendar, LIMIT)
        self.assertEqual(both.status, "gap")  # the pair needs both, and one did not exist yet
        alone, _ = latest_common([other], rows, self.cutoff(11), self.calendar, LIMIT)
        self.assertEqual((alone.status, alone.session), ("resolved", date(2026, 11, 11)))

    def test_loaders_and_derived_series_drop_what_the_file_holds_from_before_the_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            csv_path, chart = Path(temp) / "index.csv", Path(temp) / "chart.json"
            csv_path.write_text("DATE,CLOSE\n" + "".join(f"{s:%m/%d/%Y},{s.day}\n" for s in SESSIONS), encoding="utf-8")
            stamps = [int(datetime(s.year, s.month, s.day, 9, 30, tzinfo=NEW_YORK).timestamp()) for s in SESSIONS]
            chart.write_text(json.dumps({"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"}, "timestamp": stamps,
                             "indicators": {"quote": [{"open": [1.0] * len(SESSIONS), "close": [2.0] * len(SESSIONS)}]}}]}}), encoding="utf-8")
            first = lambda rows: min(o.session for o in rows)
            self.assertEqual((first(load_daily_closes(csv_path, self.undated)), first(load_daily_closes(csv_path, self.dated))), (SESSIONS[0], LAUNCH))
            self.assertEqual((first(load_closes(chart, self.undated)), first(load_closes(chart, self.dated))), (SESSIONS[0], LAUNCH))
            self.assertEqual(first(load_daily(chart, self.dated, self.undated.model_copy(update={"series_id": "x:close"}))), SESSIONS[0])
            self.assertEqual({o.session for o in load_daily(chart, self.dated, self.dated.model_copy(update={"series_id": "x:close"}))},
                             {s for s in SESSIONS if s >= LAUNCH})
        other = [observe(self.undated.model_copy(update={"series_id": "y:index"}), s, 2.0) for s in SESSIONS]
        derived = self.dated.model_copy(update={"series_id": "derived:x_over_y"})
        self.assertEqual(first(ratio_series(derived, self.rows, other)), LAUNCH)  # full inputs, but the derived series has its own floor

    def test_an_unset_date_leaves_a_binding_hash_unchanged_and_a_set_one_changes_it(self):
        self.assertNotEqual(binding_digest(self.undated), binding_digest(self.dated))
        catalogue = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text(encoding="utf-8"))
        pinned = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/factor_definitions.json").read_text(encoding="utf-8"))["bindings_sha256"]
        bindings = {s["series_id"]: SeriesBinding(**s) for s in catalogue["series"]}
        self.assertEqual({i: binding_digest(bindings[i]) for i in pinned}, pinned)  # every pinned binding still matches
        for series in catalogue["series"]:  # a series derived from others cannot start before the latest of its inputs
            if series["series_id"].startswith("derived:"):
                self.assertIsNotNone(bindings[series["series_id"]].first_published, series["series_id"])


if __name__ == "__main__":
    unittest.main()
