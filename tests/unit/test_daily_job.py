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
    from quant_contracts.forecast import BetaGap, BetaTicket
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
        self.last, self.broken, self.short_history, self.no_close = SESSION, None, None, None

    def fetcher(self, url):
        if self.broken and self.broken in url:
            raise OSError("connection reset")
        equity = [s for s in self.all_sessions if START <= s <= self.last]
        if "/chart/" in url:
            stamps = [int(datetime.combine(d, time(9, 30), NEW_YORK).timestamp()) for d in equity]
            closes = [100.0 + 0.1 * i for i in range(len(equity))]
            if self.short_history and f"/chart/{self.short_history}?" in url:  # too little history for a 200-session trend
                stamps, closes = stamps[-5:], closes[-5:]
            if self.no_close and f"/chart/{self.no_close}?" in url:  # the session's bar is served unfinished, with no quote to fill it
                return json.dumps({"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"}, "timestamp": stamps,
                                                         "indicators": {"quote": [{"open": closes, "close": closes[:-1] + [None]}]}}]}}).encode()
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
        self.assertEqual((sorted(log["stages"]), len(log["stages"]["rule"]["written"]), log["stages"]["rule"]["failed"]), (["backup", "rule", "shadow", "snapshot"], 2, {}))
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
        self.assertEqual(lines, ["2026-11-23 rule fund_soxx: no ticket; snapshot: the snapshot command exited 1",
                                 "2026-11-23 rule fund_qqq: no ticket; snapshot: the snapshot command exited 1"])

    def earlier_rule_ticket(self, subject, day, state, issuance):
        template = BetaTicket.model_validate_json(next((self.records / "pc-a/tickets/rule/fund_soxx").glob("*.json")).read_text())
        fields = {**json.loads(template.model_dump_json()), "ticket_id": f"beta:fund:{subject}:{day}", "subject_id": f"fund:{subject}", "session": str(day), "state": state,
                  "target_exposure": {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0}[state], "issuance": issuance,
                  "decision_time": datetime.combine(day, time(17, 0), NEW_YORK).isoformat(), "issued_at": datetime.combine(day, time(19, 0), NEW_YORK).isoformat()}
        path = self.records / f"pc-a/tickets/rule/fund_{subject}/{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BetaTicket(**fields).model_dump_json())

    def test_a_rule_with_a_missing_input_holds_the_last_live_ticket_never_a_replay_and_records_a_gap_with_none(self):
        self.short_history = "QQQ"  # QQQ's trend has no value; SOXX is unaffected
        self.last = date(2026, 11, 20)
        legacy = self.records / "pc-a/tickets/rule/fund_qqq/2026-11-17.json"  # a ticket from before issuance was recorded: when it was written is unknown
        legacy.parent.mkdir(parents=True)
        fields = json.loads((ROOT / "artifacts/tickets/QQQ/2026-10-23.json").read_text())
        fields.update(session="2026-11-17", ticket_id="beta:fund:qqq:2026-11-17", decision_time="2026-11-17T17:00:00-05:00", issued_at=None, issuance=None)
        legacy.write_text(BetaTicket(**fields).model_dump_json())
        self.assertEqual(self.job("--session", "2026-11-20", now=datetime(2026, 11, 20, 19, 0, tzinfo=NEW_YORK)), 0)
        # It was not held: what follows is the gap, exactly as with no earlier ticket at all.
        gap = BetaGap.model_validate_json((self.records / "pc-a/gaps/rule/fund_qqq/2026-11-20.json").read_text())
        self.assertEqual((gap.inputs["trend_200"], "no earlier live ticket to hold" in gap.reason), (None, True))  # nothing to hold: a gap, not a state
        self.assertEqual(list((self.records / "pc-a/tickets/rule/fund_qqq").glob("2026-11-20.json")), [])
        self.assertTrue((self.records / "pc-a/tickets/rule/fund_soxx/2026-11-20.json").is_file())  # the other subject got its ticket
        self.assertEqual(self.job("--session", "2026-11-20", now=datetime(2026, 11, 20, 20, 0, tzinfo=NEW_YORK)), 0)  # a rerun keeps the gap
        self.assertEqual(self.logs(date(2026, 11, 20))[-1]["stages"]["rule"]["kept"], ["beta:fund:soxx:2026-11-20", "gap:rule:fund:qqq:2026-11-20"])
        # A live GREEN ticket, then a RED one written late. The late one never took effect, so the next held ticket is GREEN.
        self.earlier_rule_ticket("qqq", date(2026, 11, 18), "GREEN", "live")
        self.earlier_rule_ticket("qqq", date(2026, 11, 19), "RED", "replay")
        self.last = SESSION
        self.assertEqual(self.job(), 0)
        held = BetaTicket.model_validate_json((self.records / "pc-a/tickets/rule/fund_qqq/2026-11-23.json").read_text())
        self.assertEqual((held.state, held.target_exposure, held.held, held.stressed_rules), ("GREEN", 1.0, True, ()))

    def test_a_snapshot_that_differs_from_the_recorded_manifest_writes_no_ticket(self):
        self.assertEqual(self.job(), 0)
        base = self.records / "pc-a"
        taken = self.root / "artifacts/snapshots/2026-11-23/manifest.json"  # the snapshot on disk is no longer the one the records describe
        other = json.loads(taken.read_text())
        other["downloaded_at"] = "2026-11-23T23:00:00+00:00"
        taken.write_text(json.dumps(other))
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(20, 0), NEW_YORK)), 1)
        log = self.logs()[-1]
        self.assertEqual((sorted(log["stages"]), "no ticket is written" in log["stages"]["manifest"]["failed"]), (["backup", "manifest", "snapshot"], True))  # no ticket stage ran
        # The manifest on record is not replaced, here or on the remote, and the log of the failed run is published.
        remote = lambda path: self.git(self.remote, "show", f"main:{path}")
        self.assertNotEqual(json.loads(remote("pc-a/manifests/2026-11-23.json"))["downloaded_at"], other["downloaded_at"])
        self.assertEqual(remote("pc-a/manifests/2026-11-23.json"), (base / "manifests/2026-11-23.json").read_text().rstrip("\n"))
        self.assertEqual(len([n for n in self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines() if n.startswith("pc-a/runs/2026-11-23/")]), 2)

    def test_a_committed_record_changed_or_deleted_on_disk_is_never_published(self):
        self.assertEqual(self.job(), 0)
        base = self.records / "pc-a"
        (base / "tickets/rule/fund_qqq/2026-11-23.json").unlink()
        recorded = json.loads((base / "manifests/2026-11-23.json").read_text())
        recorded["downloaded_at"] = "2026-11-23T23:00:00+00:00"
        (base / "manifests/2026-11-23.json").write_text(json.dumps(recorded))
        for attempt in (20, 21):  # the run fails, and a retry does not write the deleted ticket again or push the edit
            self.assertEqual(self.job(now=datetime.combine(SESSION, time(attempt, 0), NEW_YORK)), 1)
            self.assertFalse((base / "tickets/rule/fund_qqq/2026-11-23.json").exists())
        self.assertIn("no ticket is written", self.logs()[-1]["stages"]["records"]["failed"])
        # The next morning the operator is told which record blocked issuance, not that nothing was logged.
        (base / "tickets/rule/fund_soxx/2026-11-23.json").unlink()
        self.last = date(2026, 11, 24)
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 1)
        morning = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        blocked = [line for line in morning if line.startswith("2026-11-24 ")]
        self.assertEqual(len(blocked), 2)
        self.assertTrue(all("no ticket; records: " in line and "pc-a/" in line and "no ticket is written" in line for line in blocked))
        remote = lambda path: self.git(self.remote, "show", f"main:{path}")
        self.assertEqual(json.loads(remote("pc-a/manifests/2026-11-23.json")), json.loads((self.root / "artifacts/snapshots/2026-11-23/manifest.json").read_text()))
        self.assertIn("beta:fund:qqq:2026-11-23", remote("pc-a/tickets/rule/fund_qqq/2026-11-23.json"))
        self.assertEqual(len([n for n in self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines() if n.startswith("pc-a/runs/2026-11-23/")]), 3)
        self.assertEqual(self.git(self.records, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "main"))

    def test_a_committed_ticket_that_was_moved_stops_issuance_and_its_new_place_is_not_published(self):
        self.last = date(2026, 11, 20)
        self.assertEqual(self.job("--session", "2026-11-20", now=datetime(2026, 11, 20, 19, 0, tzinfo=NEW_YORK)), 0)
        self.git(self.records, "mv", "pc-a/tickets/rule/fund_qqq/2026-11-20.json", "pc-a/tickets/rule/fund_qqq/2026-11-20.moved")  # staged, as git mv does
        self.last, self.short_history = SESSION, "QQQ"  # QQQ has a missing input today: with the ticket gone it would be published as a gap
        self.assertEqual(self.job(), 1)
        self.assertIn("fund_qqq/2026-11-20.json", self.logs()[-1]["stages"]["records"]["failed"])
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertEqual([n for n in names if "2026-11-23" in n and "/runs/" not in n], [])  # no ticket and no gap for today
        self.assertTrue("pc-a/tickets/rule/fund_qqq/2026-11-20.json" in names and not any(n.endswith(".moved") for n in names))
        self.assertFalse((self.records / "pc-a/gaps").exists())

    def test_a_retry_never_publishes_a_file_that_changed_after_an_earlier_run_staged_it(self):
        hook = self.records / ".git/hooks/pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.assertEqual(self.job(), 1)  # the tickets are staged; the commit failed
        hook.unlink()
        ticket = self.records / "pc-a/tickets/rule/fund_qqq/2026-11-23.json"
        staged = self.git(self.records, "show", ":pc-a/tickets/rule/fund_qqq/2026-11-23.json")
        changed = json.loads(ticket.read_text())
        changed.update(state="RED", target_exposure=0.0)  # the file on disk no longer says what was issued at 19:00
        ticket.write_text(json.dumps(changed))
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(19, 15), NEW_YORK)), 1)
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertNotIn("pc-a/tickets/rule/fund_qqq/2026-11-23.json", names)  # neither version is published
        self.assertEqual([n for n in names if n.startswith("pc-a/") and "/runs/" not in n], [])  # while that stands, only failure logs are published
        self.assertEqual(self.git(self.records, "show", ":pc-a/tickets/rule/fund_qqq/2026-11-23.json"), staged)  # the staged version is kept as it was
        self.assertEqual(json.loads(ticket.read_text()), changed)  # and the file is not touched either
        self.assertIn("fund_qqq/2026-11-23.json", self.logs()[-1]["stages"]["records"]["failed"])  # the run says which record
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(19, 30), NEW_YORK)), 1)
        # The file on disk says "live", but it is not a ticket on record: the morning check says so and does not count it.
        morning = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 24, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(len(morning), 1)
        self.assertTrue(morning[0].startswith("2026-11-23 rule fund_qqq: not a ticket on record; an earlier run staged one and the file has changed since"))
        self.assertNotIn("pc-a/tickets/rule/fund_qqq/2026-11-23.json", self.git(self.remote, "ls-tree", "-r", "--name-only", "main"))  # nor on a further retry

    def test_a_ticket_renamed_to_another_session_is_never_published_and_is_not_counted(self):
        self.assertEqual(self.job(), 0)
        folder = self.records / "pc-a/tickets/rule/fund_qqq"
        (folder / "2026-11-23.json").rename(folder / "2026-11-24.json")  # outside git: a deletion and an untracked file
        self.last = date(2026, 11, 24)
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 1)
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertNotIn("pc-a/tickets/rule/fund_qqq/2026-11-24.json", names)  # the 23rd's ticket does not appear under the 24th
        self.assertIn("pc-a/tickets/rule/fund_qqq/2026-11-23.json", names)
        self.assertEqual([n for n in names if "2026-11-24" in n and "/runs/" not in n], [])  # of the 24th, only the failure log is published
        self.assertEqual(len([n for n in names if n.startswith("pc-a/runs/2026-11-24/")]), 1)
        morning = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertTrue(any(line.startswith("2026-11-24 rule fund_qqq: no ticket; ") and line.endswith("it holds another decision, rule fund_qqq 2026-11-23") for line in morning))
        self.assertTrue(any(line.startswith("2026-11-23 rule fund_qqq: no ticket") for line in morning))
        self.assertTrue(any(line.startswith("2026-11-24 rule fund_soxx: no ticket; records: ") for line in morning))

    def test_a_copy_of_another_sessions_ticket_is_neither_reused_nor_published(self):
        self.assertEqual(self.job(), 0)
        folder = self.records / "pc-a/tickets/rule/fund_qqq"
        shutil.copyfile(folder / "2026-11-23.json", folder / "2026-11-24.json")  # the original stays: no committed record has changed
        self.last = date(2026, 11, 24)
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 1)  # not a success
        log = self.logs(date(2026, 11, 24))[-1]
        self.assertIn("holds another decision, rule fund_qqq 2026-11-23", log["stages"]["rule"]["failed"]["QQQ"])
        self.assertEqual(len(log["stages"]["rule"]["written"]), 1)  # SOXX still gets the day's ticket
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertNotIn("pc-a/tickets/rule/fund_qqq/2026-11-24.json", names)  # the 23rd's payload is not published under the 24th
        self.assertIn("pc-a/tickets/rule/fund_soxx/2026-11-24.json", names)
        self.assertEqual(json.loads((folder / "2026-11-24.json").read_text())["session"], "2026-11-23")  # and the stray file is left for the operator
        morning = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(morning, ["2026-11-24 rule fund_qqq: no ticket; pc-a/tickets/rule/fund_qqq/2026-11-24.json is not this decision's ticket: "
                                   "it holds another decision, rule fund_qqq 2026-11-23"])

    def test_the_morning_check_fails_when_the_records_cannot_be_verified(self):
        self.assertEqual(self.job(), 0)
        ticket = self.records / "pc-a/tickets/rule/fund_qqq/2026-11-23.json"
        edited = json.loads(ticket.read_text())
        edited.update(state="RED", target_exposure=0.0)
        ticket.write_text(json.dumps(edited))
        (self.records / ".git").rename(self.records / ".git.off")  # git cannot say what was committed
        try:
            morning = datetime(2026, 11, 24, 8, 0, tzinfo=NEW_YORK)
            lines = missed_runs.report(self.root, self.records, "pc-a", None, morning)
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("the records could not be verified, so no ticket is counted: "))
            self.assertEqual(missed_runs.main(["--records", str(self.records), "--machine", "pc-a"], root=self.root, now=morning), 1)
        finally:
            (self.records / ".git.off").rename(self.records / ".git")
        self.assertEqual(missed_runs.report(self.root, self.records, "pc-a", None, morning),
                         ["2026-11-23 rule fund_qqq: not the ticket on record; the file differs from what was committed"])

    def test_an_unreadable_ticket_is_reported_by_path_and_the_rest_are_still_checked(self):
        self.assertEqual(self.job(), 0)
        base = self.records / "pc-a"
        committed = base / "tickets/rule/fund_qqq/2026-11-23.json"
        committed.write_text(committed.read_text()[:40])  # truncated: git already knows it changed
        self.last = date(2026, 11, 24)
        fresh = base / "tickets/rule/fund_soxx/2026-11-24.json"  # and a new file that is not a ticket at all
        fresh.write_text("{not json")
        lines = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertTrue(lines[1].startswith("2026-11-24 rule fund_soxx: no ticket; pc-a/tickets/rule/fund_soxx/2026-11-24.json is not this decision's ticket: it is not a complete BetaTicket"))
        self.assertEqual(lines, ["2026-11-23 rule fund_qqq: not the ticket on record; the file differs from what was committed",
                                 lines[1],
                                 "2026-11-24 rule fund_qqq: no ticket; the job did not run for this session"])

    def test_one_subject_never_holds_another_subjects_ticket(self):
        self.last = date(2026, 11, 20)
        self.assertEqual(self.job("--session", "2026-11-20", now=datetime(2026, 11, 20, 19, 0, tzinfo=NEW_YORK)), 0)
        base = self.records / "pc-a"
        # SOXX's live GREEN ticket of the 19th is dropped into QQQ's history, and QQQ's own ticket of the 20th is the only real one.
        self.earlier_rule_ticket("soxx", date(2026, 11, 19), "GREEN", "live")
        stray = base / "tickets/rule/fund_qqq/2026-11-21.json"  # later than QQQ's own, so it would be picked first
        shutil.copyfile(base / "tickets/rule/fund_soxx/2026-11-19.json", stray)
        own = json.loads((base / "tickets/rule/fund_qqq/2026-11-20.json").read_text())
        self.last, self.short_history = SESSION, "QQQ"  # QQQ has a missing input today
        self.assertEqual(self.job(), 1)  # the stray file is reported and not published
        held = BetaTicket.model_validate_json((base / "tickets/rule/fund_qqq/2026-11-23.json").read_text())
        self.assertEqual((held.held, held.state, held.subject_id), (True, own["state"], "fund:qqq"))  # QQQ holds QQQ's own last live ticket
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertTrue("pc-a/tickets/rule/fund_qqq/2026-11-23.json" in names and "pc-a/tickets/rule/fund_qqq/2026-11-21.json" not in names)

    def test_with_only_another_subjects_ticket_in_its_history_a_missing_input_is_a_gap(self):
        self.last, self.short_history = date(2026, 11, 20), "QQQ"
        folder = self.records / "pc-a/tickets/rule/fund_qqq"
        self.assertEqual(self.job("--session", "2026-11-20", now=datetime(2026, 11, 20, 19, 0, tzinfo=NEW_YORK)), 0)  # QQQ: a gap; SOXX: a ticket
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.records / "pc-a/tickets/rule/fund_soxx/2026-11-20.json", folder / "2026-11-20.json")  # SOXX's ticket, in QQQ's history
        self.last = SESSION
        self.assertEqual(self.job(), 1)
        self.assertTrue((self.records / "pc-a/gaps/rule/fund_qqq/2026-11-23.json").is_file())  # nothing of QQQ's to hold: a gap, not SOXX's position
        self.assertFalse((folder / "2026-11-23.json").exists())
        self.assertNotIn("pc-a/tickets/rule/fund_qqq/2026-11-20.json", self.git(self.remote, "ls-tree", "-r", "--name-only", "main"))

    def test_a_ticket_with_a_field_missing_is_refused_at_issuance_publication_and_the_morning_check(self):
        self.assertEqual(self.job(), 0)
        self.last = date(2026, 11, 24)
        ticket = self.records / "pc-a/tickets/rule/fund_qqq/2026-11-24.json"  # the right subject and date, and no state
        fields = json.loads((self.records / "pc-a/tickets/rule/fund_qqq/2026-11-23.json").read_text())
        fields.update(session="2026-11-24", ticket_id="beta:fund:qqq:2026-11-24", decision_time="2026-11-24T17:00:00-05:00", issued_at="2026-11-24T18:00:00-05:00")
        del fields["state"]
        ticket.write_text(json.dumps(fields))
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 1)
        self.assertIn("is not a complete BetaTicket", self.logs(date(2026, 11, 24))[-1]["stages"]["rule"]["failed"]["QQQ"])  # issuance
        self.assertNotIn("pc-a/tickets/rule/fund_qqq/2026-11-24.json", self.git(self.remote, "ls-tree", "-r", "--name-only", "main"))  # publication
        morning = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))  # the morning check
        self.assertEqual(len(morning), 1)
        self.assertTrue(morning[0].startswith("2026-11-24 rule fund_qqq: no ticket; pc-a/tickets/rule/fund_qqq/2026-11-24.json is not this decision's ticket: it is not a complete BetaTicket"))

    def test_a_gap_record_that_is_not_one_is_reported_and_the_rest_are_still_checked(self):
        self.last, self.short_history = date(2026, 11, 20), "QQQ"
        self.assertEqual(self.job("--session", "2026-11-20", now=datetime(2026, 11, 20, 19, 0, tzinfo=NEW_YORK)), 0)  # QQQ's record is a committed gap
        gap = self.records / "pc-a/gaps/rule/fund_qqq/2026-11-20.json"
        gap.write_text("null")
        lines = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 21, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(lines, ["2026-11-20 rule fund_qqq: not the gap on record; the file differs from what was committed"])  # git's word first, no parsing
        self.git(self.records, "checkout", "--", "pc-a/gaps/rule/fund_qqq/2026-11-20.json")
        self.last = SESSION
        fresh = self.records / "pc-a/gaps/rule/fund_soxx/2026-11-23.json"  # a new file git has no opinion on, in SOXX's place
        fresh.parent.mkdir(parents=True)
        fresh.write_text("null")
        lines = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 24, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(len(lines), 3)  # the 20th's real gap, the unreadable one by its path, and QQQ's missing ticket of the 23rd
        self.assertTrue(lines[0].startswith("2026-11-20 rule fund_qqq: a gap; "))
        self.assertTrue(lines[1].startswith("2026-11-23 rule fund_soxx: no ticket; pc-a/gaps/rule/fund_soxx/2026-11-23.json is not this decision's gap: it is not a complete BetaGap"))
        self.assertTrue(lines[2].startswith("2026-11-23 rule fund_qqq: no ticket; "))

    def test_a_ticket_moved_onto_a_lock_name_is_still_a_moved_ticket(self):
        self.assertEqual(self.job(), 0)
        self.git(self.records, "mv", "pc-a/tickets/rule/fund_qqq/2026-11-23.json", "pc-a/tickets/rule/fund_qqq/hidden.lock")
        self.last = date(2026, 11, 24)
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 1)
        self.assertIn("fund_qqq/2026-11-23.json", self.logs(date(2026, 11, 24))[-1]["stages"]["records"]["failed"])
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertTrue("pc-a/tickets/rule/fund_qqq/2026-11-23.json" in names and not any(n.endswith(".lock") for n in names))
        self.assertEqual([n for n in names if "2026-11-24" in n and "/runs/" not in n], [])

    def test_records_that_could_not_be_staged_are_never_reported_as_pushed_and_nothing_else_is_committed(self):
        (self.records / "unrelated.txt").write_text("staged by someone else\n")
        self.git(self.records, "add", "unrelated.txt")
        lock = self.records / ".git/index.lock"
        lock.write_text("")  # git cannot stage while this is there
        self.assertEqual(self.job(), 1)
        self.assertIn("pc-a/tickets/rule/fund_qqq/2026-11-23.json", self.git(self.records, "status", "--porcelain", "--untracked-files=all"))  # still only on disk
        self.assertNotIn("pc-a", self.git(self.remote, "ls-tree", "-r", "--name-only", "main"))
        lock.unlink()
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(19, 15), NEW_YORK)), 0)
        self.assertEqual(self.git(self.records, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "main"))
        committed = self.git(self.records, "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertTrue(committed and all(name.startswith("pc-a/") for name in committed))  # the job's commit holds its own records only
        self.assertEqual(self.git(self.records, "diff", "--cached", "--name-only"), "unrelated.txt")  # what was staged before is still only staged

    def test_a_backup_that_failed_is_completed_by_the_next_run_and_does_not_hold_up_the_tickets(self):
        real, copy = snapshot.back_up, self.root / "run/backup/snapshots/2026-11-23"

        def failing(folder, target):
            raise OSError("disk full")

        snapshot.back_up = failing
        try:
            self.assertEqual(self.job(), 1)  # reported, so the scheduler runs it again
        finally:
            snapshot.back_up = real
        self.assertEqual((len(list((self.records / "pc-a").glob("tickets/rule/*/2026-11-23.json"))), copy.exists()), (2, False))
        self.assertIn("disk full", self.logs()[0]["stages"]["backup"]["failed"])
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(19, 15), NEW_YORK)), 0)
        self.assertTrue((copy / "SHA256SUMS").is_file() and (copy / "manifest.json").read_bytes() == (self.root / "artifacts/snapshots/2026-11-23/manifest.json").read_bytes())
        self.assertEqual(self.logs()[1]["stages"]["backup"], {"written": "run/backup/snapshots/2026-11-23"})

    def test_a_subject_that_fails_from_the_first_session_is_reported_and_stays_reported(self):
        self.no_close = "QQQ"
        self.assertEqual(self.job(), 1)  # SOXX gets its ticket; QQQ's close for the session is not there
        self.assertTrue((self.records / "pc-a/tickets/rule/fund_soxx/2026-11-23.json").is_file())
        first_morning = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 24, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(len(first_morning), 1)
        self.assertTrue(first_morning[0].startswith("2026-11-23 rule fund_qqq: no ticket; rule: ") and "no QQQ close" in first_morning[0])
        self.no_close, self.last = None, date(2026, 11, 24)
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 0)  # QQQ's first ticket
        self.assertTrue((self.records / "pc-a/tickets/rule/fund_qqq/2026-11-24.json").is_file())
        later = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(later, first_morning)  # the missed day is still on the list, and only that

    def test_a_ticket_edited_on_disk_cannot_shape_a_new_ticket(self):
        self.last = date(2026, 11, 20)
        self.assertEqual(self.job("--session", "2026-11-20", now=datetime(2026, 11, 20, 19, 0, tzinfo=NEW_YORK)), 0)
        yesterday = self.records / "pc-a/tickets/rule/fund_qqq/2026-11-20.json"
        edited = json.loads(yesterday.read_text())
        self.assertEqual((edited["state"], edited["issuance"]), ("GREEN", "live"))
        edited.update(state="RED", target_exposure=0.0)  # the committed ticket says GREEN; the file on disk now says RED
        yesterday.write_text(json.dumps(edited))
        self.last, self.short_history = SESSION, "QQQ"  # today QQQ has a missing input, so its ticket would hold yesterday's state
        self.assertEqual(self.job(), 1)
        log = self.logs()[-1]
        self.assertIn("no ticket is written", log["stages"]["records"]["failed"])
        self.assertTrue("rule" not in log["stages"] and "shadow" not in log["stages"])
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertEqual([n for n in names if "/tickets/" in n and "2026-11-23" in n], [])  # no ticket for today, for either subject
        self.assertTrue(any(n.startswith("pc-a/runs/2026-11-23/") for n in names))  # the failure log is published
        self.assertIn('"state": "GREEN"', self.git(self.remote, "show", "main:pc-a/tickets/rule/fund_qqq/2026-11-20.json"))  # the remote keeps what was issued
        self.assertFalse((self.records / "pc-a/tickets/rule/fund_soxx/2026-11-23.json").exists())

    def test_records_staged_by_a_run_whose_commit_failed_are_published_by_the_next_run(self):
        hook = self.records / ".git/hooks/pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.assertEqual(self.job(), 1)  # staging worked, the commit did not
        staged = self.git(self.records, "diff", "--cached", "--name-only").splitlines()
        self.assertIn("pc-a/tickets/rule/fund_qqq/2026-11-23.json", staged)
        self.assertNotIn("pc-a", self.git(self.remote, "ls-tree", "-r", "--name-only", "main"))
        hook.unlink()
        self.assertEqual(self.job(now=datetime.combine(SESSION, time(19, 15), NEW_YORK)), 0)
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertTrue({"pc-a/tickets/rule/fund_qqq/2026-11-23.json", "pc-a/tickets/rule/fund_soxx/2026-11-23.json", "pc-a/manifests/2026-11-23.json"} <= set(names))
        self.assertEqual(len([n for n in names if n.startswith("pc-a/runs/2026-11-23/")]), 2)  # both runs' logs
        self.assertEqual(self.git(self.records, "status", "--porcelain", "--untracked-files=all").strip(), "?? pc-a/job.lock")

    def test_a_session_a_gate_was_registered_for_before_this_machine_first_ran_is_reported(self):
        self.last = date(2026, 11, 24)
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 0)  # the first run here
        path = self.root / "artifacts/forward/shadow_gates/registration.json"
        registration = json.loads(path.read_text())
        registration["status"] = "Registered for this test"
        registration["subjects"] = [{**s, "first_session": "2026-11-23"} for s in registration["subjects"]]  # the gates were due from the 23rd
        path.write_text(json.dumps(registration))
        lines = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual((len(lines), sum(line.startswith("2026-11-23 ") for line in lines)), (14, 7))  # seven shadow decisions on each day
        self.assertTrue(all("the job did not run for this session" in line for line in lines if line.startswith("2026-11-23 ")))
        self.assertFalse(any(" rule " in line for line in lines))  # the rule's record here starts with the first run, on the 24th

    def test_a_gate_registered_later_raises_nothing_about_the_sessions_before_its_first_session(self):
        self.assertEqual(self.job(), 0)  # one session recorded with the rule only
        self.last = date(2026, 11, 24)
        self.assertEqual(self.job("--session", "2026-11-24", now=datetime(2026, 11, 24, 19, 0, tzinfo=NEW_YORK)), 0)
        path = self.root / "artifacts/forward/shadow_gates/registration.json"
        registration = json.loads(path.read_text())
        registration["status"] = "Registered for this test"  # the shadow gates start on the 24th
        registration["subjects"] = [{**s, "first_session": "2026-11-24"} for s in registration["subjects"]]
        path.write_text(json.dumps(registration))
        lines = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual(len(lines), 7)  # the seven shadow decisions of the 24th, which no run wrote
        self.assertTrue(all(line.startswith("2026-11-24 ") and "rule" not in line.split(":")[0] for line in lines))  # nothing about the 23rd
        # One gate joins SOXX later still, with its own date: it is expected from that date, not from SOXX's.
        registration["subjects"] = [{**s, "gate_first_session": {"W-dd": "2026-11-25"}} if s["subject"] == "SOXX" else s for s in registration["subjects"]]
        path.write_text(json.dumps(registration))
        lines = missed_runs.report(self.root, self.records, "pc-a", None, datetime(2026, 11, 25, 8, 0, tzinfo=NEW_YORK))
        self.assertEqual((len(lines), any("W-dd fund_soxx" in line for line in lines)), (6, False))


if __name__ == "__main__":
    unittest.main()
