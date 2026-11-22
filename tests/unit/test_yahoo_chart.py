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
    from quant_pit_data.connectors.yahoo_chart import ex_dividend_dates, load_daily, split_dates
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


if __name__ == "__main__":
    unittest.main()
