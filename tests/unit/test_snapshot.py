"""The snapshot command: written once, all or nothing, and every repair to a served file is recorded."""

from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/cli"))
for package in ("quant_contracts", "quant_pit_data"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
import snapshot
try:
    from quant_contracts.pit import SeriesBinding
    from quant_pit_data.asof import load_daily_closes
    from quant_pit_data.connectors.yahoo_chart import load_daily
except ModuleNotFoundError:
    SeriesBinding = None

NEW_YORK = ZoneInfo("America/New_York")
SESSIONS = [date(2026, 11, d) for d in (18, 19, 20, 23, 24)]
SESSION, EVENING = date(2026, 11, 23), datetime(2026, 11, 23, 19, 0, tzinfo=NEW_YORK)
stamp = lambda day, hour, minute=0: int(datetime.combine(day, time(hour, minute), NEW_YORK).timestamp())


def binding(series_id, url, value_time="16:00"):
    return {"series_id": series_id, "label": series_id, "anchors": ["test"], "source_url": url, "availability": "news_only",
            "timezone": "America/New_York", "value_time": value_time, "publication_delay_minutes": 15, "availability_basis": "assumed",
            "revisable": True, "basis_note": "Synthetic fixture."}


CATALOGUE = {"series": [binding("yahoo:abc:open", snapshot.YAHOO + "ABC", "09:30"), binding("yahoo:abc:close", snapshot.YAHOO + "ABC"),
                        binding("cboe:vix", snapshot.CBOE + "VIX_History.csv", "16:15"), binding("treasury:real_yield_10y", snapshot.TREASURY_PAGE, "15:30"),
                        binding("derived:a_over_b", "derived from yahoo:abc:close and cboe:vix"), binding("maker:revenue", "https://example.invalid/news")]}


def chart(days, closes, price=None, at=None):
    meta = {"exchangeTimezoneName": "America/New_York", "currentTradingPeriod": {"regular": {"end": stamp(SESSION, 16)}}}
    if price is not None:
        meta.update(regularMarketPrice=price, regularMarketTime=at)
    return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": [stamp(d, 9, 30) for d in days],
                                             "indicators": {"quote": [{"open": [100.0 + d.day for d in days], "high": [200.0] * len(days),
                                                                       "low": [50.0] * len(days), "close": closes}]}}]}}).encode()


def cboe(days):
    return ("DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(f"{d:%m/%d/%Y},1.000000,2.000000,0.500000,{20 + d.day}.000000\n" for d in days)).encode()


def treasury(year, days):
    return ('Date,"5 YR","7 YR","10 YR"\n' + "".join(f"{d:%m/%d/%Y},1.00,1.50,{d.day / 10:.2f}\n" for d in reversed(days) if d.year == year)).encode()


@unittest.skipIf(SeriesBinding is None, "pydantic is not installed")
class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, content in ((snapshot.CATALOGUE, CATALOGUE), (snapshot.CALENDAR, {"sessions": [str(s) for s in SESSIONS]})):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(json.dumps(content))
        done = [s for s in SESSIONS if s <= SESSION]
        self.served = {"ABC?period1": chart(done, [101.0 + d.day for d in done]), "VIX_History.csv": cboe(done),
                       **{f"/{y}/all": treasury(y, done) if y == 2026 else treasury(y, [date(y, 1, 5)]) for y in range(2003, 2027)}}
        self.asked = []

    def fetcher(self, url):
        self.asked.append(url)
        for key, data in self.served.items():
            if key in url:
                if isinstance(data, Exception):
                    raise data
                return data
        raise ValueError(f"unexpected request {url}")

    def run_it(self, *arguments, now=EVENING):
        return snapshot.main(list(arguments), root=self.root, fetcher=self.fetcher, pause=0.0, now=now)

    def manifest(self, session=SESSION):
        return json.loads((self.root / "artifacts/snapshots" / str(session) / "manifest.json").read_text())

    def test_a_snapshot_is_complete_pinned_readable_and_written_once(self):
        self.assertEqual(self.run_it(), 0)
        folder, manifest = self.root / "artifacts/snapshots/2026-11-23", self.manifest()
        self.assertEqual((manifest["session"], sorted(manifest["files"])), ("2026-11-23", ["ABC.json", "VIX.csv", "treasury_real_yield_10y.csv"]))
        self.assertEqual(set(manifest["not_downloaded"]), {"derived:a_over_b", "maker:revenue"})
        for name, entry in manifest["files"].items():
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), entry["sha256"], name)
            self.assertEqual((entry["last_session"], "raw_sha256" in entry), ("2026-11-23", False))  # nothing needed repair
        self.assertEqual(manifest["files"]["ABC.json"]["series_ids"], ["yahoo:abc:open", "yahoo:abc:close"])
        b = {s["series_id"]: SeriesBinding(**s) for s in CATALOGUE["series"]}
        prices = load_daily(folder / "ABC.json", b["yahoo:abc:open"], b["yahoo:abc:close"])
        self.assertEqual(max(o.session for o in prices), SESSION)
        self.assertEqual(load_daily_closes(folder / "VIX.csv", b["cboe:vix"])[-1].value, 43.0)
        self.assertEqual(load_daily_closes(folder / "treasury_real_yield_10y.csv", b["treasury:real_yield_10y"])[-1].value, 2.3)
        copy = self.root / "run/backup/snapshots/2026-11-23"
        sums = dict(reversed(line.split("  ./")) for line in (copy / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(sums["manifest.json"], hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest())
        before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
        self.assertEqual(self.run_it(), 1)  # a second run changes nothing
        self.assertEqual({p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}, before)

    def test_a_missing_close_is_filled_only_from_a_price_stamped_after_the_regular_close(self):
        done = [s for s in SESSIONS if s <= SESSION]
        closes = [101.0 + d.day for d in done[:-1]] + [None]
        self.served["ABC?period1"] = chart(done, closes, price=363.6, at=stamp(SESSION, 16, 0) + 2)
        self.assertEqual(self.run_it("--no-backup"), 0)
        entry, folder = self.manifest()["files"]["ABC.json"], self.root / "artifacts/snapshots/2026-11-23"
        self.assertEqual((entry["filled_session_close"]["value"], entry["last_session"], entry["rows_without_a_close"]), (363.6, "2026-11-23", 0))
        self.assertEqual(hashlib.sha256((folder / "raw/ABC.json").read_bytes()).hexdigest(), entry["raw_sha256"])  # the served bytes are kept
        self.assertEqual(json.loads((folder / "ABC.json").read_text())["chart"]["result"][0]["indicators"]["quote"][0]["close"][-1], 363.6)
        self.assertFalse((self.root / "run/backup/snapshots").exists())

    def test_a_price_from_inside_the_session_is_never_taken_as_the_close(self):
        done = [s for s in SESSIONS if s <= SESSION]
        self.served["ABC?period1"] = chart(done, [101.0 + d.day for d in done[:-1]] + [None], price=363.6, at=stamp(SESSION, 15, 59))
        self.assertEqual(self.run_it("--no-backup"), 0)
        entry = self.manifest()["files"]["ABC.json"]
        self.assertEqual((entry["last_session"], "filled_session_close" in entry, "session_close_missing" in entry), ("2026-11-20", False, True))

    def test_a_last_bar_that_is_a_live_quote_is_marked_and_left_as_served(self):
        done = [s for s in SESSIONS if s <= SESSION]
        live = json.loads(chart(done, [101.0 + d.day for d in done], price=79.94, at=stamp(SESSION, 20, 49)))
        live["chart"]["result"][0]["timestamp"][-1] = stamp(SESSION, 20, 49)  # the bar carries the quote's time, as a futures bar does
        self.served["ABC?period1"] = json.dumps(live).encode()
        self.assertEqual(self.run_it("--no-backup"), 0)
        entry = self.manifest()["files"]["ABC.json"]
        self.assertEqual((entry["last_bar_is_a_live_quote"], "raw_sha256" in entry), ("2026-11-23", False))
        self.assertEqual((self.root / "artifacts/snapshots/2026-11-23/ABC.json").read_bytes(), self.served["ABC?period1"])

    def test_a_bar_of_a_trading_period_still_open_at_download_is_marked_too(self):
        days = SESSIONS  # the 24th's bar exists already, stamped at midnight, in a period that runs to the end of the 24th
        live = json.loads(chart(days, [101.0 + d.day for d in days], price=3622.3, at=stamp(date(2026, 11, 24), 3, 4)))
        live["chart"]["result"][0]["timestamp"] = [stamp(d, 0) for d in days]
        live["chart"]["result"][0]["meta"]["currentTradingPeriod"] = {"regular": {"start": stamp(date(2026, 11, 24), 0), "end": stamp(date(2026, 11, 24), 23, 59)}}
        self.served["ABC?period1"] = json.dumps(live).encode()
        self.assertEqual(self.run_it("--no-backup", now=datetime(2026, 11, 24, 3, 14, tzinfo=NEW_YORK)), 0)
        self.assertEqual(self.manifest()["files"]["ABC.json"]["last_bar_is_a_live_quote"], "2026-11-24")

    def test_a_lagging_cboe_file_takes_the_session_row_from_yahoo_only_when_the_two_agree(self):
        done = [s for s in SESSIONS if s <= SESSION]
        self.served["VIX_History.csv"] = cboe(done[:-1])  # ends on the 20th, at 40
        self.served["%5EVIX?interval"] = chart(done, [38.0, 39.0, 40.004, 26.62])
        self.assertEqual(self.run_it("--no-backup"), 0)
        entry, folder = self.manifest()["files"]["VIX.csv"], self.root / "artifacts/snapshots/2026-11-23"
        self.assertEqual((entry["appended_session_row"]["close"], entry["appended_session_row"]["agreement_checked_on"], entry["last_session"]),
                         (26.62, "2026-11-20", "2026-11-23"))
        self.assertEqual((folder / "VIX.csv").read_text().splitlines()[-1].split(",")[0::4], ["11/23/2026", "26.620000"])
        self.assertTrue((folder / "raw/VIX.csv").is_file() and (folder / "raw/VIX_yahoo.json").is_file())

    def test_a_lagging_cboe_file_is_left_alone_when_the_other_source_disagrees(self):
        done = [s for s in SESSIONS if s <= SESSION]
        self.served["VIX_History.csv"] = cboe(done[:-1])
        self.served["%5EVIX?interval"] = chart(done, [38.0, 39.0, 40.5, 26.62])  # half a point apart on the 20th
        self.assertEqual(self.run_it("--no-backup"), 0)
        entry = self.manifest()["files"]["VIX.csv"]
        self.assertEqual((entry["last_session"], "appended_session_row" in entry), ("2026-11-20", False))
        self.assertIn("disagree", entry["session_row_missing"])

    def test_one_failing_source_publishes_nothing_and_the_day_can_be_retried(self):
        good = self.served["VIX_History.csv"]
        self.served["VIX_History.csv"] = OSError("connection reset")
        self.assertEqual(self.run_it(), 1)
        self.assertEqual([p.name for p in (self.root / "artifacts/snapshots").iterdir() if p.name != "2026-11-22"], [])  # no folder, no partial folder
        self.assertFalse((self.root / "run/backup/snapshots").exists())
        self.served["VIX_History.csv"] = good
        self.assertEqual(self.run_it(), 0)

    def test_a_response_that_is_not_the_expected_file_fails_the_run(self):
        self.served["VIX_History.csv"] = b"<html><body>307 Temporary Redirect</body></html>"
        self.assertEqual(self.run_it(), 1)

    def test_earlier_treasury_years_are_reused_from_the_previous_snapshot(self):
        self.assertEqual(self.run_it("--session", "2026-11-20", "--no-backup"), 0)
        self.assertEqual(sum("/all" in u for u in self.asked), 24)  # the first snapshot downloads every year
        self.asked.clear()
        self.assertEqual(self.run_it("--no-backup"), 0)
        self.assertEqual([u for u in self.asked if "/all" in u], [snapshot.TREASURY_YEAR.format(year=2026)])
        first, second = (self.manifest(d)["files"]["treasury_real_yield_10y.csv"] for d in (date(2026, 11, 20), SESSION))
        self.assertEqual(second["years_reused_from"], "2026-11-20: 2003 to 2025")
        self.assertEqual({y: h for y, h in second["years"].items() if y != "2026"}, {y: h for y, h in first["years"].items() if y != "2026"})

    def test_a_session_that_has_not_closed_or_is_not_a_session_is_refused(self):
        self.assertEqual(self.run_it("--session", "2026-11-24"), 2)
        self.assertEqual(self.run_it("--session", "2026-11-22"), 2)
        self.assertEqual(self.run_it(now=datetime(2026, 11, 23, 15, 59, tzinfo=NEW_YORK)), 0)  # before the close: the 20th is the latest
        self.assertTrue((self.root / "artifacts/snapshots/2026-11-20").is_dir())


if __name__ == "__main__":
    unittest.main()
