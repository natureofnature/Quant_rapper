"""Reading a saved Yahoo chart response. Skipped when pydantic is not installed."""

from datetime import date, datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_contracts.pit import SeriesBinding
    from quant_pit_data.connectors.yahoo_chart import ex_dividend_dates, load_closes, load_daily, monthly_mean_closes, split_dates
    from quant_pit_data.asof import ratio_series
except ModuleNotFoundError:
    SeriesBinding = None

NEW_YORK = ZoneInfo("America/New_York")


def stamp(day: int, hour: int = 9, minute: int = 30) -> int:
    return int(datetime(2026, 11, day, hour, minute, tzinfo=NEW_YORK).timestamp())


@unittest.skipIf(SeriesBinding is None, "pydantic is not installed")
class YahooChartTests(unittest.TestCase):
    def test_sessions_are_exchange_dates_and_missing_values_are_left_out(self):
        def binding(series_id, value_time):
            return SeriesBinding(series_id=series_id, label=series_id, anchors=("test",),
                                 source_url="https://example.invalid", availability="news_only",
                                 timezone="America/New_York", value_time=value_time, publication_delay_minutes=15,
                                 availability_basis="assumed", revisable=True, basis_note="Synthetic fixture.")

        chart = {"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "America/New_York"},
            "timestamp": [stamp(9), stamp(10)],
            "indicators": {"quote": [{"open": [10.0, None], "close": [11.0, 12.0]}]},
            "events": {"dividends": {"a": {"date": stamp(10)}}, "splits": {"b": {"date": stamp(9)}}}}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chart.json"
            path.write_text(json.dumps(chart), encoding="utf-8")
            rows = load_daily(path, binding("x:open", "09:30"), binding("x:close", "16:00"))
            self.assertEqual([(r.series_id, r.session, r.value) for r in rows],
                             [("x:open", date(2026, 11, 9), 10.0), ("x:close", date(2026, 11, 9), 11.0),
                              ("x:close", date(2026, 11, 10), 12.0)])
            self.assertEqual((ex_dividend_dates(path), split_dates(path)), ([date(2026, 11, 10)], [date(2026, 11, 9)]))

    def test_a_month_is_read_in_the_exchange_timezone(self):
        """Midnight in London on 1 July is 23:00 UTC on 30 June: the quote belongs to July."""
        london = ZoneInfo("Europe/London")
        local_midnight = lambda month, day: int(datetime(2024, month, day, tzinfo=london).timestamp())
        chart = {"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "Europe/London"},
            "timestamp": [local_midnight(6, 28), local_midnight(7, 1), local_midnight(7, 2)],
            "indicators": {"quote": [{"close": [30.0, 32.0, None]}]}}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chart.json"
            path.write_text(json.dumps(chart), encoding="utf-8")
            self.assertEqual(monthly_mean_closes(path), {"2024-06": 30.0, "2024-07": 32.0})

    def test_a_ratio_series_is_knowable_when_its_later_input_is(self):
        def binding(series_id, value_time, delay):
            return SeriesBinding(series_id=series_id, label=series_id, anchors=("test",), source_url="https://example.invalid",
                                 availability="news_only", timezone="America/New_York", value_time=value_time,
                                 publication_delay_minutes=delay, availability_basis="assumed", revisable=True, basis_note="Synthetic fixture.")
        chart = lambda closes: {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"},
                                                      "timestamp": [stamp(9), stamp(10), stamp(11)], "indicators": {"quote": [{"close": closes}]}}]}}
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for name, closes in (("a", [10.0, None, 30.0]), ("b", [5.0, 4.0, 0.0])):
                paths.append(Path(folder) / f"{name}.json")
                paths[-1].write_text(json.dumps(chart(closes)), encoding="utf-8")
            top, bottom = load_closes(paths[0], binding("a", "16:00", 15)), load_closes(paths[1], binding("b", "16:00", 120))
            self.assertEqual([o.session.day for o in top], [9, 11])  # a row without a close is left out
            self.assertEqual([o.session.day for o in bottom], [9, 10])  # and so is a close that is not positive
            ratio = ratio_series(binding("a_over_b", "16:00", 0), top, bottom)
            self.assertEqual([(o.session.day, o.value) for o in ratio], [(9, 2.0)])
            self.assertEqual(ratio[0].available_at, bottom[0].available_at)  # 18:00, the later of the two
            self.assertEqual(ratio[0].availability_basis, "assumed")


if __name__ == "__main__":
    unittest.main()
