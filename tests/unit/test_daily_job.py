"""The daily job: one command from download to pushed records, safe to run again, and honest about what failed.

The repository's catalogue, rule registration and calendars are used; every download is synthetic, and the
records repository is a temporary git checkout with a bare repository as its remote.
"""

from datetime import date, datetime, time
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/cli"))
try:
    import daily_job
    import missed_runs
    import snapshot
    from quant_contracts.forecast import BetaTicket
except ModuleNotFoundError:
    daily_job = None

NEW_YORK = ZoneInfo("America/New_York")
SESSION, START = date(2026, 11, 23), date(2025, 1, 2)
EVENING = datetime.combine(SESSION, time(19, 0), NEW_YORK)
COPIED = ("artifacts/ablations/sector_risk_baseline/registration.json", "artifacts/forward/shadow_gates/registration.json",
          "knowledge_base/prior_knowledge/bindings/series_bindings.json", "knowledge_base/prior_knowledge/bindings/factor_definitions.json",
          "knowledge_base/prior_knowledge/bindings/event_schedules.json")


@unittest.skipIf(daily_job is None, "pydantic is not installed")
class DailyJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        top = Path(self.temp.name)
        self.root, self.records, self.remote = top / "code", top / "records", top / "remote.git"
        for name in (*COPIED, *snapshot.SHARED):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, self.root / name)
        git = lambda where, *a: subprocess.run(["git", "-C", str(where), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *a],
                                               check=True, capture_output=True, text=True).stdout.strip()
        self.git = git
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.records)], check=True)
        git(self.records, "config", "user.name", "test"); git(self.records, "config", "user.email", "test@example.invalid")
        (self.records / "README.md").write_text("records\n")
        git(self.records, "add", "-A"); git(self.records, "commit", "-q", "-m", "start"); git(self.records, "remote", "add", "origin", str(self.remote))
        git(self.records, "push", "-q", "-u", "origin", "main")
        sessions = lambda path: [date.fromisoformat(s) for s in json.loads((ROOT / path).read_text())["sessions"]]
        self.all_sessions, self.bond_days = sessions(snapshot.SHARED[0]), sessions(snapshot.SHARED[1])
        self.last, self.broken = SESSION, None

    def fetcher(self, url):
        if self.broken and self.broken in url:
            raise OSError("connection reset")
        equity = [s for s in self.all_sessions if START <= s <= self.last]
        if "/chart/" in url:
            stamps = [int(datetime.combine(d, time(9, 30), NEW_YORK).timestamp()) for d in equity]
            closes = [100.0 + 0.1 * i for i in range(len(equity))]
            return json.dumps({"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"}, "timestamp": stamps,
                                                     "indicators": {"quote": [{"open": closes, "close": closes}]}}]}}).encode()
        if "_History.csv" in url:
            level = 20.0 if "VIX_History" in url else 22.0
            return ("DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(f"{d:%m/%d/%Y},{level},{level},{level},{level}\n" for d in equity)).encode()
        year = int(url.split("/all")[0].rsplit("/", 1)[1])
        days = [d for d in self.bond_days if d.year == year and (d <= self.last if year >= START.year else d == min(x for x in self.bond_days if x.year == year))]
        return ('Date,"5 YR","10 YR"\n' + "".join(f"{d:%m/%d/%Y},1.00,{2.0 + 0.01 * (d.toordinal() % 7):.2f}\n" for d in reversed(days))).encode()

    def job(self, *arguments, now=EVENING):
        return daily_job.main(["--records", str(self.records), *arguments], root=self.root, fetcher=self.fetcher, now=now, machine="pc-a", pause=0.0, fetch_pause=0.0)

    def logs(self, session=SESSION):
        return [json.loads(p.read_text()) for p in sorted((self.records / "pc-a/runs" / str(session)).glob("*.json"))]

    def test_one_run_takes_the_snapshot_writes_the_rule_tickets_and_pushes_the_records(self):
        self.assertEqual(self.job(), 0)
        base = self.records / "pc-a"
        tickets = {p.parent.name: BetaTicket.model_validate_json(p.read_text()) for p in base.glob("tickets/rule/*/2026-11-23.json")}
        self.assertEqual(sorted(tickets), ["fund_qqq", "fund_soxx"])
        self.assertEqual({(t.state, t.issuance, t.issued_at) for t in tickets.values()}, {("GREEN", "live", EVENING)})
        self.assertEqual((base / "manifests/2026-11-23.json").read_bytes(), (self.root / "artifacts/snapshots/2026-11-23/manifest.json").read_bytes())
        log = self.logs()[0]
        self.assertEqual((sorted(log["stages"]), len(log["stages"]["rule"]["written"]), log["stages"]["rule"]["failed"]), (["rule", "shadow", "snapshot"], 2, {}))
        self.assertIn("draft", log["stages"]["shadow"]["skipped"])  # a draft registration is skipped and said, not failed
        self.assertFalse((base / "tickets/E-dd").exists())
        self.assertFalse((self.root / "artifacts/tickets").exists())  # nothing goes to the code checkout
        # Committed and pushed: the remote holds exactly what is on disk, and the lock is not part of it.
        self.assertEqual(self.git(self.records, "status", "--porcelain", "--untracked-files=all").strip(), "?? pc-a/job.lock")  # the lock is never committed
        self.assertNotIn("job.lock", self.git(self.records, "ls-files"))
        self.assertEqual(self.git(self.records, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "main"))

    def test_a_second_run_for_the_same_session_writes_nothing_twice(self):
        self.assertEqual(self.job(), 0)
        base = self.records / "pc-a"
        before = {p: p.read_bytes() for p in base.glob("tickets/rule/*/*.json")}
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(21, 0), NEW_YORK)), 0)
        self.assertEqual({p: p.read_bytes() for p in base.glob("tickets/rule/*/*.json")}, before)  # the tickets keep their first time
        first, second = self.logs()
        self.assertEqual((second["stages"]["snapshot"], len(second["stages"]["rule"]["kept"]), second["stages"]["rule"]["written"]),
                         ({"kept": "already taken; it matches its manifest"}, 2, []))
        self.assertEqual(len(first["stages"]["rule"]["written"]), 2)  # each run has its own log; the first is not edited

    def test_a_source_that_fails_is_logged_and_the_run_is_completed_by_the_next_one(self):
        self.broken = "VIX_History.csv"
        self.assertEqual(self.job(), 1)
        base = self.records / "pc-a"
        self.assertFalse((base / "tickets").exists() or (base / "manifests").exists())  # no data, no decision
        self.assertIn("failed", self.logs()[0]["stages"]["snapshot"])
        self.assertEqual(self.git(self.records, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "main"))  # the log of the failure is on record too
        self.broken = None
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(19, 15), NEW_YORK)), 0)
        self.assertEqual(len(list(base.glob("tickets/rule/*/2026-11-23.json"))), 2)

    def test_a_push_that_fails_leaves_the_records_committed_and_the_next_run_sends_them(self):
        self.git(self.records, "remote", "set-url", "origin", str(self.remote) + ".gone")
        self.assertEqual(self.job(), 1)
        self.assertEqual(len(list((self.records / "pc-a").glob("tickets/rule/*/2026-11-23.json"))), 2)  # the tickets exist and are committed
        self.assertNotEqual(self.git(self.records, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "main"))
        self.git(self.records, "remote", "set-url", "origin", str(self.remote))
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(19, 15), NEW_YORK)), 0)
        self.assertEqual(self.git(self.records, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "main"))

    def test_a_ticket_written_after_the_next_open_is_a_replay_and_the_morning_check_reports_what_is_missing(self):
        check = lambda now, *a: missed_runs.report(self.root, self.records, "pc-a", None, now)
        self.assertEqual(check(EVENING), ["pc-a has recorded nothing yet"])
        self.assertEqual(self.job(), 0)
        self.assertEqual(check(datetime(2026, 11, 24, 8, 0, tzinfo=NEW_YORK)), [])  # the next morning: every decision of the 23rd has a live ticket
        # The 24th passes with no run. On the 25th the job is run late, after the open, for the 24th.
        self.last = date(2026, 11, 24)
        late = datetime(2026, 11, 25, 9, 45, tzinfo=NEW_YORK)
        morning = check(datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(morning, ["2026-11-24 rule fund_soxx: no ticket; the job did not run for this session",
                                   "2026-11-24 rule fund_qqq: no ticket; the job did not run for this session"])
        self.assertEqual(self.job("--session", "2026-11-24", now=late), 0)
        ticket = BetaTicket.model_validate_json((self.records / "pc-a/tickets/rule/fund_qqq/2026-11-24.json").read_text())
        self.assertEqual((ticket.issuance, ticket.issued_at), ("replay", late))
        after = check(datetime(2026, 11, 25, 10, 0, tzinfo=NEW_YORK))
        self.assertEqual(len(after), 2)
        self.assertTrue(all("written late" in line and "not part of the forward record" in line for line in after))
        self.assertEqual(missed_runs.main(["--records", str(self.records), "--machine", "pc-a"], root=self.root, now=datetime(2026, 11, 25, 10, 0, tzinfo=NEW_YORK)), 1)

    def test_a_snapshot_failure_is_what_the_morning_check_names(self):
        self.broken = "VIX_History.csv"
        self.assertEqual(self.job(), 1)
        lines = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 24, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("no ticket; snapshot: the snapshot command exited 1" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
