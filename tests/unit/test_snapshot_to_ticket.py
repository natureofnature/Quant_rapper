"""End to end: a snapshot written by the command is enough to build the rule's ticket for its session.

The repository's own catalogue, rule registration and calendars are used; every download is synthetic.
"""

from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/cli"))
for package in ("quant_contracts", "quant_pit_data", "quant_evaluator", "quant_causal_graph", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
import snapshot
try:
    from quant_loop.tickets import emit
except ModuleNotFoundError:
    emit = None

NEW_YORK = ZoneInfo("America/New_York")
SESSION, START = date(2026, 11, 23), date(2025, 1, 2)
COPIED = ("artifacts/ablations/sector_risk_baseline/registration.json", "knowledge_base/prior_knowledge/bindings/series_bindings.json",
          "knowledge_base/prior_knowledge/bindings/factor_definitions.json", *snapshot.SHARED)


@unittest.skipIf(emit is None, "pydantic is not installed")
class SnapshotToTicketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in COPIED:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, self.root / name)
        sessions = lambda path: [date.fromisoformat(s) for s in json.loads((ROOT / path).read_text())["sessions"]]
        self.equity = [s for s in sessions(snapshot.SHARED[0]) if START <= s <= SESSION]
        self.bond_days = sessions(snapshot.SHARED[1])

    def fetcher(self, url):
        if "/chart/" in url:  # every symbol rises a little each session
            stamps = [int(datetime.combine(d, time(9, 30), NEW_YORK).timestamp()) for d in self.equity]
            closes = [100.0 + 0.1 * i for i in range(len(self.equity))]
            return json.dumps({"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"}, "timestamp": stamps,
                                                     "indicators": {"quote": [{"open": closes, "close": closes}]}}]}}).encode()
        if "_History.csv" in url:  # the near index stays below the three-month one
            level = 20.0 if "VIX_History" in url else 22.0
            return ("DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(f"{d:%m/%d/%Y},{level},{level},{level},{level}\n" for d in self.equity)).encode()
        year = int(url.split("/all")[0].rsplit("/", 1)[1])
        days = [d for d in self.bond_days if d.year == year and (d <= SESSION if year >= START.year else d == min(x for x in self.bond_days if x.year == year))]
        return ('Date,"5 YR","10 YR"\n' + "".join(f"{d:%m/%d/%Y},1.00,{2.0 + 0.01 * (d.toordinal() % 7):.2f}\n" for d in reversed(days))).encode()

    def test_the_rule_ticket_is_built_from_a_snapshot_the_command_wrote(self):
        evening = datetime.combine(SESSION, time(19, 0), NEW_YORK)
        self.assertEqual(snapshot.main(["--no-backup"], root=self.root, fetcher=self.fetcher, pause=0.0, now=evening), 0)
        folder = self.root / "artifacts/snapshots" / str(SESSION)
        manifest = json.loads((folder / "manifest.json").read_text())
        ticket = emit(self.root, folder, "QQQ", SESSION, now=evening)
        self.assertEqual((ticket.session, ticket.state, ticket.target_exposure, ticket.held, ticket.issuance), (SESSION, "GREEN", 1.0, False, "live"))
        self.assertTrue(all(v is not None for v in ticket.inputs.values()))
        self.assertLess(ticket.inputs["vix_term_structure"], 1.0)
        # The ticket records the hash of every file it read: the downloads from the snapshot, the calendars from the pins.
        for name, value in ticket.snapshot_sha256.items():
            pinned = manifest["files"].get(name) or manifest["shared"][name]
            self.assertEqual(value, pinned["sha256"], name)
        self.assertLessEqual({Path(p).name for p in snapshot.SHARED[:2]}, set(ticket.snapshot_sha256))

    def test_a_ticket_from_a_repaired_close_carries_the_record_and_is_refused_without_it(self):
        evening, served = datetime.combine(SESSION, time(19, 0), NEW_YORK), self.fetcher

        def with_unfinished_close(url):  # QQQ's bar for the session has no close yet; the response's own quote, stamped 16:00:02, fills it
            data = served(url)
            if "/chart/QQQ" not in url:
                return data
            whole = json.loads(data)
            result = whole["chart"]["result"][0]
            price, result["indicators"]["quote"][0]["close"] = result["indicators"]["quote"][0]["close"][-1], result["indicators"]["quote"][0]["close"][:-1] + [None]
            result["meta"].update(regularMarketPrice=price, regularMarketTime=int(datetime.combine(SESSION, time(16, 0, 2), NEW_YORK).timestamp()))
            return json.dumps(whole).encode()

        self.assertEqual(snapshot.main(["--no-backup"], root=self.root, fetcher=with_unfinished_close, pause=0.0, now=evening), 0)
        folder = self.root / "artifacts/snapshots" / str(SESSION)
        manifest = json.loads((folder / "manifest.json").read_text())
        ticket = emit(self.root, folder, "QQQ", SESSION, now=evening)
        self.assertEqual(ticket.snapshot_sha256["QQQ.record.json"], manifest["files"]["QQQ.json"]["record_file"]["sha256"])
        record = folder / "QQQ.record.json"
        kept = record.read_text()
        record.write_text(kept.replace("16:00:02", "15:00:02"))
        with self.assertRaisesRegex(SystemExit, "the record of QQQ.json does not match the snapshot manifest"):
            emit(self.root, folder, "QQQ", SESSION, now=evening)
        record.unlink()
        with self.assertRaisesRegex(SystemExit, "the record of QQQ.json is missing"):
            emit(self.root, folder, "QQQ", SESSION, now=evening)

    def test_a_shared_calendar_that_changed_since_the_snapshot_is_refused(self):
        evening = datetime.combine(SESSION, time(19, 0), NEW_YORK)
        self.assertEqual(snapshot.main(["--no-backup"], root=self.root, fetcher=self.fetcher, pause=0.0, now=evening), 0)
        calendar = self.root / snapshot.SHARED[0]
        calendar.write_text(calendar.read_text() + "\n")
        with self.assertRaisesRegex(SystemExit, "us_equity_sessions_2002_2026.json does not match the snapshot manifest"):
            emit(self.root, self.root / "artifacts/snapshots" / str(SESSION), "QQQ", SESSION, now=evening)


if __name__ == "__main__":
    unittest.main()
