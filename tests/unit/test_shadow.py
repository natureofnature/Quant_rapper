"""Shadow tickets: built from one snapshot, never early, never under a draft or by other code, and written once.

The repository's catalogue and calendars are used. Every download is synthetic and the registration is cut down
(six inputs, short training) so that the whole path runs in seconds.
"""

from datetime import date, datetime, time
import hashlib
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
for package in ("quant_contracts", "quant_pit_data", "quant_evaluator", "quant_causal_graph", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
import snapshot
try:
    import numpy as np
    from quant_contracts.evaluation import Factor
    from quant_contracts.forecast import ShadowGap, ShadowTicket
    from quant_contracts.pit import SeriesBinding
    from quant_loop.shadow import REGISTRATION, emit
except ModuleNotFoundError:
    emit = None

NEW_YORK = ZoneInfo("America/New_York")
SESSION, START = date(2026, 11, 23), date(2023, 1, 3)
EVENING = datetime.combine(SESSION, time(19, 0), NEW_YORK)
BINDINGS = "knowledge_base/prior_knowledge/bindings"
COPIED = (*(f"{BINDINGS}/{n}" for n in ("series_bindings.json", "factor_definitions.json", "event_schedules.json")), *snapshot.SHARED)
KEPT = ("vix_term_structure", "real_yield_10y_change_20_rank_250", "trend_200", "vix_log", "sessions_to_fomc", "roc_20")


def small(registration: dict, kept=None) -> dict:
    """The repository's registration with six inputs and a short history, so that a test fits in seconds."""
    r = json.loads(json.dumps(registration))
    r["inputs"]["input_definitions"] = {k: v for k, v in r["inputs"]["input_definitions"].items() if k in (kept or KEPT)}
    r["inputs"]["first_session"] = "2025-06-02"
    r["parameters"].update(min_train=60, min_positive=3, gate={**r["parameters"]["gate"], "window": 40, "min_scores": 20})
    r["parameters"]["tree"].update(n_estimators=10, min_child_days=10)
    r["subjects"] = [{**s, "gates": ["W-dd", "E-dd"] if s["subject"] == "QQQ" else ["E-dd"]} for s in r["subjects"] if s["subject"] in ("QQQ", "TSM")]
    r["gates"]["E-dd"]["seeds"] = [0, 1]
    return r


@unittest.skipIf(emit is None, "numpy or pydantic is not installed")
class ShadowTicketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name) / "code"
        for name in COPIED:
            (cls.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, cls.root / name)
        cls.registration = small(json.loads((ROOT / REGISTRATION).read_text()))
        (cls.root / REGISTRATION).parent.mkdir(parents=True)
        (cls.root / REGISTRATION).write_text(json.dumps(cls.registration, indent=1) + "\n")
        sessions = lambda path: [date.fromisoformat(s) for s in json.loads((ROOT / path).read_text())["sessions"]]
        cls.equity = [s for s in sessions(snapshot.SHARED[0]) if START <= s <= SESSION]
        cls.bond_days = sessions(snapshot.SHARED[1])
        cls.late_close = None  # a symbol whose session close is served unfinished, with a quote stamped at this time
        cls.short_history = None  # a symbol served with only its last few sessions
        cls.a_day_behind = None  # a symbol whose file ends on the session before, as an evening download of a round-the-clock market does
        assert snapshot.main(["--no-backup"], root=cls.root, fetcher=cls.fetcher, pause=0.0, now=EVENING) == 0
        cls.folder = cls.root / "artifacts/snapshots" / str(SESSION)
        batch = emit(cls.root, cls.folder, SESSION, None, now=EVENING, machine="test-machine", dry_run=True)
        cls.tickets, cls.digest = batch.tickets, batch.digest

    @classmethod
    def fetcher(cls, url):
        walk = lambda key, n, scale: np.exp(np.cumsum(np.random.default_rng(int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)).normal(0.0, scale, n)))
        if "/chart/" in url:
            symbol = url.split("/chart/")[1].split("?")[0]
            closes = [float(v) for v in 100.0 * walk(symbol, len(cls.equity), 0.02)]
            meta = {"exchangeTimezoneName": "America/New_York"}
            if cls.late_close and symbol == cls.late_close[0]:
                meta.update(regularMarketPrice=closes[-1], regularMarketTime=int(cls.late_close[1].timestamp()))
                closes = closes[:-1] + [None]
            stamps = [int(datetime.combine(d, time(9, 30), NEW_YORK).timestamp()) for d in cls.equity]
            if symbol == cls.short_history:
                stamps, closes = stamps[-5:], closes[-5:]
            if symbol == cls.a_day_behind:
                stamps, closes = stamps[:-1], closes[:-1]
            return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": stamps, "indicators": {"quote": [{"open": [c or 1.0 for c in closes], "close": closes}]}}]}}).encode()
        if "_History.csv" in url:
            name = url.rsplit("/", 1)[1]
            levels = 20.0 * walk(name, len(cls.equity), 0.05)
            return ("DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(f"{d:%m/%d/%Y},{v:.4f},{v:.4f},{v:.4f},{v:.4f}\n" for d, v in zip(cls.equity, levels))).encode()
        year = int(url.split("/all")[0].rsplit("/", 1)[1])
        days = [d for d in cls.bond_days if d.year == year and (START <= d <= SESSION if year >= START.year else d == min(x for x in cls.bond_days if x.year == year))]
        values = dict(zip([d for d in cls.bond_days if START <= d <= SESSION], 2.0 + np.cumsum(np.random.default_rng(7).normal(0.0, 0.03, 2000))))
        return ('Date,"5 YR","10 YR"\n' + "".join(f"{d:%m/%d/%Y},1.00,{values.get(d, 2.0):.2f}\n" for d in reversed(days))).encode()

    def copy_of_code(self, name, change=None):
        """A separate copy of the test's code folder, with its own snapshot taken after ``change`` is applied."""
        root = Path(self.temp.name) / name
        shutil.copytree(self.root, root, ignore=shutil.ignore_patterns("2026-11-23"))
        if change:
            change(root)
        self.assertEqual(snapshot.main(["--no-backup"], root=root, fetcher=self.fetcher, pause=0.0, now=EVENING), 0)
        return root, root / "artifacts/snapshots" / str(SESSION)

    def registered(self, name, issuer="pc-a", first="2026-11-23"):
        """A git checkout whose registration is registered: it pins a code commit, an issuer and the configuration files."""
        root = Path(self.temp.name) / name
        shutil.copytree(self.root, root)
        git = lambda *a: subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *a],
                                        check=True, capture_output=True).stdout.decode().strip()
        (root / "packages").mkdir()
        (root / "packages/code.py").write_text("VERSION = 1\n")
        (root / ".gitignore").write_text("artifacts/snapshots/2026-11-23/\n")
        git("init", "-q"); git("add", "-A"); git("commit", "-q", "-m", "code")
        pinned = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in COPIED}
        registration = {**self.registration, "status": "Registered for this test", "code": {"commit": git("rev-parse", "HEAD")}, "issuer": issuer, "pinned_files": pinned,
                        "subjects": [{**s, "first_session": first} for s in self.registration["subjects"]]}
        (root / REGISTRATION).write_text(json.dumps(registration, indent=1) + "\n")
        return root, root / "artifacts/snapshots" / str(SESSION), git, registration

    def test_every_registered_gate_gets_a_ticket_built_from_the_snapshot(self):
        self.assertEqual(sorted((t.subject_id, t.gate) for t in self.tickets), [("company:tsmc", "E-dd"), ("fund:qqq", "E-dd"), ("fund:qqq", "W-dd")])
        manifest = json.loads((self.folder / "manifest.json").read_text())
        for t in self.tickets:
            self.assertEqual((t.session, t.issuance, t.machine, t.held, t.decision_time), (SESSION, "live", "test-machine", False, datetime.combine(SESSION, time(17, 0), NEW_YORK)))
            self.assertEqual((sorted(t.inputs), all(v is not None for v in t.inputs.values())), (sorted(KEPT), True))
            self.assertEqual(len(t.seed_scores), 1 if t.gate == "W-dd" else 2)
            self.assertAlmostEqual(t.score, sum(t.seed_scores) / len(t.seed_scores), places=12)
            # Every file the inputs were built from is named with its hash: downloads, calendars and the bindings.
            for name, value in t.inputs_sha256.items():
                pinned = manifest["files"].get(name) or manifest["shared"].get(name)
                self.assertEqual(value, pinned["sha256"] if pinned else hashlib.sha256((self.root / BINDINGS / name).read_bytes()).hexdigest(), name)
            self.assertLessEqual({"VIX.csv", "VIX3M.csv", "treasury_real_yield_10y.csv", "series_bindings.json", "event_schedules.json"}, set(t.inputs_sha256))
        qqq = {t.gate: t for t in self.tickets if t.subject_id == "fund:qqq"}
        self.assertEqual(qqq["W-dd"].seed_scores[0], qqq["E-dd"].seed_scores[0])  # the same seed-0 fit serves both gates
        self.assertEqual(qqq["W-dd"].inputs, qqq["E-dd"].inputs)
        self.assertNotIn("GCF.json", qqq["E-dd"].inputs_sha256)  # a file these inputs never read is not required
        tsm = next(t for t in self.tickets if t.subject_id == "company:tsmc")
        self.assertTrue("QQQ.json" in qqq["E-dd"].inputs_sha256 and "QQQ.json" not in tsm.inputs_sha256 and "TSM.json" in tsm.inputs_sha256)
        alone = emit(self.root, self.folder, SESSION, None, now=EVENING, machine="test-machine", dry_run=True, only=["TSM"]).tickets[0]
        self.assertEqual(alone, tsm)  # a subject's ticket does not depend on which others were built in the same run

    def test_the_same_snapshot_gives_the_same_scores_and_a_draft_is_never_written(self):
        again = emit(self.root, self.folder, SESSION, None, now=EVENING, machine="test-machine", dry_run=True)
        self.assertEqual((again.tickets, again.digest), (self.tickets, self.digest))  # the digest is what two machines compare
        records = Path(self.temp.name) / "records"
        with self.assertRaisesRegex(SystemExit, "the registration is a draft"):
            emit(self.root, self.folder, SESSION, records, now=EVENING, machine="test-machine")
        self.assertFalse(records.exists())

    def test_no_ticket_is_written_before_the_decision_time(self):
        with self.assertRaisesRegex(SystemExit, "before the decision time"):
            emit(self.root, self.folder, SESSION, None, now=datetime.combine(SESSION, time(16, 59), NEW_YORK), dry_run=True)

    def test_live_or_replay_is_decided_when_the_ticket_is_written_not_when_the_run_starts(self):
        started, finished = datetime(2026, 11, 24, 9, 28, tzinfo=NEW_YORK), datetime(2026, 11, 24, 9, 32, tzinfo=NEW_YORK)
        readings = iter([started])  # the first reading is the start of the run; every later one is after the work is done
        batch = emit(self.root, self.folder, SESSION, None, dry_run=True, only=["TSM"], clock=lambda: next(readings, finished))
        ticket = batch.tickets[0]
        self.assertEqual((ticket.issued_at, ticket.issuance, ticket.decision_time.time()), (finished, "replay", time(17, 0)))  # the open was at 09:30
        early = emit(self.root, self.folder, SESSION, None, dry_run=True, only=["TSM"], now=started)
        self.assertEqual(early.tickets[0].issuance, "live")

    def test_a_subject_whose_close_arrived_after_the_cutoff_gets_no_ticket_and_the_others_do(self):
        type(self).late_close = ("TSM", datetime.combine(SESSION, time(17, 30), NEW_YORK))
        try:
            root, folder = self.copy_of_code("late")
        finally:
            type(self).late_close = None
        batch = emit(root, folder, SESSION, None, now=EVENING, dry_run=True)
        self.assertEqual({t.subject_id for t in batch.tickets}, {"fund:qqq"})  # TSM's close was stamped 17:30: nothing stands in for it

    def test_a_subject_with_no_complete_history_is_recorded_as_a_gap_and_the_other_subjects_still_get_tickets(self):
        type(self).short_history = "QQQ"  # QQQ has its close for the session, but too little history for its trend or its 20-session change, ever
        try:
            root, folder = self.copy_of_code("no_history")
        finally:
            type(self).short_history = None
        batch = emit(root, folder, SESSION, None, now=EVENING, machine="test-machine", dry_run=True)
        self.assertEqual(sorted((g.subject_id, g.gate, g.missing_inputs) for g in batch.gaps),
                         [("fund:qqq", "E-dd", ("trend_200", "roc_20")), ("fund:qqq", "W-dd", ("trend_200", "roc_20"))])
        self.assertTrue(all("an input was not knowable" in g.reason for g in batch.gaps))
        tsm = next(t for t in self.tickets if t.subject_id == "company:tsmc")
        self.assertEqual((batch.tickets, batch.errors), ([tsm], []))  # TSM's ticket is the one it gets on its own

    def test_a_series_whose_file_ends_a_day_before_the_session_does_not_stop_the_run(self):
        with_dollar = Path(self.temp.name) / "with_dollar.json"  # one more input, read on its own dates: the dollar index
        with_dollar.write_text(json.dumps(small(json.loads((ROOT / REGISTRATION).read_text()), kept=(*KEPT, "dollar_roc_20"))))
        run = lambda root, folder: emit(root, folder, SESSION, None, now=EVENING, machine="test-machine", dry_run=True, only=["TSM"], registration_path=with_dollar).tickets[0]
        whole = run(self.root, self.folder)
        type(self).a_day_behind = "DX-Y.NYB"  # its last finished value is the session before, as in an evening download
        try:
            root, folder = self.copy_of_code("a_day_behind")
        finally:
            type(self).a_day_behind = None
        ticket = run(root, folder)
        # The session's own value is knowable only after the cutoff, so the decision reads the same values either way.
        self.assertIsNotNone(ticket.inputs["dollar_roc_20"])
        self.assertEqual((ticket.held, ticket.inputs, ticket.score), (False, whole.inputs, whole.score))

    def test_a_session_without_a_score_carries_the_last_live_ticket_or_is_recorded_as_a_gap(self):
        def end_calendar_before_the_next_meeting(root):  # the FOMC meets on 9 December; the count to it then has no value
            calendar = json.loads((root / snapshot.SHARED[0]).read_text())
            calendar.update(last="2026-11-30", sessions=[s for s in calendar["sessions"] if s <= "2026-11-30"])
            (root / snapshot.SHARED[0]).write_text(json.dumps(calendar))

        root, folder = self.copy_of_code("short_calendar", end_calendar_before_the_next_meeting)
        records = Path(self.temp.name) / "held_records"
        run = lambda: emit(root, folder, SESSION, records, now=EVENING, machine="test-machine", dry_run=True, only=["TSM"])
        gap = run()  # no earlier ticket: the gate has said nothing in time, so it holds nothing
        self.assertEqual((gap.tickets, [type(g) for g in gap.gaps], gap.gaps[0].missing_inputs), ([], [ShadowGap], ("sessions_to_fomc",)))
        model = next(t for t in self.tickets if t.subject_id == "company:tsmc")

        def earlier(day, state, issuance):
            when = datetime.combine(day, time(19, 0), NEW_YORK)
            fields = {**json.loads(model.model_dump_json()), "session": str(day), "ticket_id": f"shadow:E-dd:company:tsmc:{day}", "state": state,
                      "target_exposure": {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0}[state], "issuance": issuance,
                      "decision_time": datetime.combine(day, time(17, 0), NEW_YORK).isoformat(), "issued_at": when.isoformat()}
            path = records / "test-machine/tickets/E-dd/company_tsmc" / f"{day}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ShadowTicket(**fields).model_dump_json())

        earlier(date(2026, 11, 20), "GREEN", "replay")  # written late: it is not a state the gate held in time
        self.assertEqual(run().tickets, [])
        earlier(date(2026, 11, 19), "RED", "live")
        held = run().tickets[0]
        # The state is the one actually published on the 19th, whatever today's recomputed history would say.
        self.assertEqual((held.state, held.target_exposure, held.held, held.score, held.seed_scores, held.inputs["sessions_to_fomc"]), ("RED", 0.0, True, None, (), None))
        self.assertEqual((held.no_score_reason, held.continues), ("missing_input", "held"))
        self.assertIsNotNone(held.inputs["vix_log"])

    def test_writing_needs_the_registered_file_code_configuration_and_issuer(self):
        root, folder, git, registration = self.registered("guarded")
        records = Path(self.temp.name) / "guarded_records"
        write = lambda machine="pc-a": emit(root, folder, SESSION, records, now=EVENING, machine=machine, only=["TSM"])
        with self.assertRaisesRegex(SystemExit, "must be the committed file"):
            write()
        git("add", "-A"); git("commit", "-q", "-m", "register")
        with self.assertRaisesRegex(SystemExit, "the registered issuer is pc-a"):
            write("pc-b")
        (root / "packages/code.py").write_text("VERSION = 2\n")
        with self.assertRaisesRegex(SystemExit, "the code differs from the registered commit"):
            write()
        (root / "packages/code.py").write_text("VERSION = 1\n")
        bindings = root / BINDINGS / "series_bindings.json"
        kept = bindings.read_text()
        catalogue = json.loads(kept)
        next(s for s in catalogue["series"] if s["series_id"] == "cboe:vix")["publication_delay_minutes"] = 200  # would move VIX past the cutoff
        bindings.write_text(json.dumps(catalogue))
        with self.assertRaisesRegex(SystemExit, "differ from the ones the registration pins: .*series_bindings.json"):
            write()
        self.assertFalse(records.exists())
        self.assertEqual(emit(root, folder, SESSION, None, now=EVENING, machine="pc-b", dry_run=True, only=["TSM"]).gaps, [])  # any machine may rehearse
        bindings.write_text(kept)
        ticket = write().tickets[0]
        written = ShadowTicket.model_validate_json((records / "pc-a/tickets/E-dd/company_tsmc/2026-11-23.json").read_text())
        self.assertEqual((written, written.code_commit, written.registration_sha256),
                         (ticket, registration["code"]["commit"], hashlib.sha256((root / REGISTRATION).read_bytes()).hexdigest()))
        self.assertEqual(written.score, next(t for t in self.tickets if t.subject_id == "company:tsmc").score)  # as the rehearsal computed it

    def test_a_batch_that_was_partly_written_is_completed_and_a_differing_ticket_is_reported_not_replaced(self):
        root, folder, git, _ = self.registered("resumed")
        git("add", "-A"); git("commit", "-q", "-m", "register")
        records = Path(self.temp.name) / "resumed_records"
        first_time, second_time = EVENING, datetime.combine(SESSION, time(21, 0), NEW_YORK)
        emit(root, folder, SESSION, records, now=first_time, machine="pc-a", only=["QQQ"])
        batch = emit(root, folder, SESSION, records, now=second_time, machine="pc-a")
        self.assertEqual(sorted(batch.kept), ["shadow:E-dd:fund:qqq:2026-11-23", "shadow:W-dd:fund:qqq:2026-11-23"])
        self.assertEqual({t.ticket_id: t.issued_at for t in batch.tickets},  # the earlier tickets keep the time they were first written
                         {"shadow:E-dd:fund:qqq:2026-11-23": first_time, "shadow:W-dd:fund:qqq:2026-11-23": first_time, "shadow:E-dd:company:tsmc:2026-11-23": second_time})
        self.assertEqual(len(list(records.rglob("2026-11-23.json"))), 3)
        again = emit(root, folder, SESSION, records, now=second_time, machine="pc-a")  # a complete batch is simply confirmed
        self.assertEqual((len(again.kept), again.tickets), (3, batch.tickets))
        path = records / "pc-a/tickets/W-dd/fund_qqq/2026-11-23.json"
        changed = json.loads(path.read_text())
        changed.update(state="GREEN" if changed["state"] != "GREEN" else "RED")
        changed["target_exposure"] = {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0}[changed["state"]]
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(SystemExit, "W-dd/fund_qqq/2026-11-23.json: a ticket is already there and differs"):
            emit(root, folder, SESSION, records, now=second_time, machine="pc-a")
        self.assertEqual(json.loads(path.read_text()), changed)  # left exactly as found

    def test_the_issued_record_decides_the_step_down_whatever_a_later_download_fills_in(self):
        model = next(t for t in self.tickets if (t.subject_id, t.gate) == ("fund:qqq", "E-dd"))
        self.assertEqual((model.level, model.continues, model.previous_ticket_sha256), (0, "first", None))  # today's score is a low one
        records = Path(self.temp.name) / "chain_records"
        folder = records / "test-machine/tickets/E-dd/fund_qqq"

        def earlier(day, issuance="live", held=False, run=()):
            fields = {**json.loads(model.model_dump_json()), "session": str(day), "ticket_id": f"shadow:E-dd:fund:qqq:{day}", "state": "RED", "target_exposure": 0.0,
                      "issuance": issuance, "decision_time": datetime.combine(day, time(17, 0), NEW_YORK).isoformat(),
                      "issued_at": datetime.combine(day, time(19, 0), NEW_YORK).isoformat(), "continues": "held" if held else "previous_session",
                      "previous_ticket_sha256": "0" * 64, "level": None if held else 0, "run": list(run)}
            if held:
                fields.update(held=True, no_score_reason="missing_input", score=None, seed_scores=[], inputs={**fields["inputs"], "sessions_to_fomc": None})
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{day}.json").write_text(ShadowTicket(**fields).model_dump_json())

        def today():
            batch = emit(self.root, self.folder, SESSION, records, now=EVENING, machine="test-machine", dry_run=True, only=["QQQ"])
            ticket = next(t for t in batch.tickets if t.gate == "E-dd")
            return ticket.state, ticket.run, ticket.continues

        # RED, then two low scores on consecutive sessions, each with a live ticket: today's low score is the third and the gate steps down.
        earlier(date(2026, 11, 19), run=(0,)); earlier(date(2026, 11, 20), run=(0, 0))
        self.assertEqual(today(), ("GREEN", (), "previous_session"))
        last = hashlib.sha256((folder / "2026-11-20.json").read_bytes()).hexdigest()
        batch = emit(self.root, self.folder, SESSION, records, now=EVENING, machine="test-machine", dry_run=True, only=["QQQ"])
        self.assertEqual(next(t for t in batch.tickets if t.gate == "E-dd").previous_ticket_sha256, last)  # each ticket names the one it continues from
        # The session before had no score when its ticket was issued, so the count was reset then. Today's snapshot holds a score
        # for that session all the same: it does not count, and the gate stays RED with a run of one.
        earlier(date(2026, 11, 20), held=True)
        self.assertEqual(today(), ("RED", (0,), "after_gap"))
        # The same when that session's ticket was written late, or never written.
        earlier(date(2026, 11, 20), issuance="replay", run=(0, 0))
        self.assertEqual(today(), ("RED", (0,), "after_gap"))
        (folder / "2026-11-20.json").unlink()
        self.assertEqual(today(), ("RED", (0,), "after_gap"))

    def test_a_snapshot_built_with_another_calendar_than_the_registered_one_is_refused(self):
        root, folder, git, _ = self.registered("other_calendar")
        git("add", "-A"); git("commit", "-q", "-m", "register")
        registered = json.loads((root / snapshot.SHARED[0]).read_text())
        shorter = {**registered, "last": "2026-11-30", "sessions": [s for s in registered["sessions"] if s <= "2026-11-30"]}
        (root / "artifacts/snapshots/2026-11-23/other_calendar.json").write_text(json.dumps(shorter))
        manifest = json.loads((folder / "manifest.json").read_text())  # the registered file is untouched; the manifest points elsewhere, with a valid hash
        name = Path(snapshot.SHARED[0]).name
        manifest["shared"][name] = {"path": "artifacts/snapshots/2026-11-23/other_calendar.json",
                                    "sha256": hashlib.sha256((root / "artifacts/snapshots/2026-11-23/other_calendar.json").read_bytes()).hexdigest()}
        (folder / "manifest.json").write_text(json.dumps(manifest))
        records = Path(self.temp.name) / "other_calendar_records"
        with self.assertRaisesRegex(SystemExit, f"differ from the ones the registration pins: {name}"):
            emit(root, folder, SESSION, records, now=EVENING, machine="pc-a", only=["TSM"])
        self.assertEqual([p.name for p in records.rglob("*") if p.is_file()], [".lock"])  # neither a ticket nor a gap

    def test_a_decision_recorded_as_a_gap_stays_a_gap_and_a_later_result_is_a_replay(self):
        root, folder, git, registration = self.registered("gap_first")
        git("add", "-A"); git("commit", "-q", "-m", "register")
        records = Path(self.temp.name) / "gap_first_records"
        gap = ShadowGap(gap_id=f"gap:E-dd:company:tsmc:{SESSION}", gate="E-dd", subject_id="company:tsmc", session=SESSION,
                        decision_time=datetime.combine(SESSION, time(17, 0), NEW_YORK), issued_at=EVENING, machine="pc-a",
                        reason="no score: an input was not knowable at the cutoff; and no earlier live ticket of this gate and subject to hold", missing_inputs=("sessions_to_fomc",),
                        registration_id="shadow_gates", registration_sha256="0" * 64, code_commit=registration["code"]["commit"], inputs_sha256={})
        path = records / "pc-a/gaps/E-dd/company_tsmc/2026-11-23.json"
        path.parent.mkdir(parents=True)
        path.write_text(gap.model_dump_json())
        later = datetime.combine(SESSION, time(22, 0), NEW_YORK)  # still before the next open: the retry could compute a ticket now
        batch = emit(root, folder, SESSION, records, now=later, machine="pc-a", only=["TSM"])
        self.assertEqual((batch.tickets, batch.gaps, batch.conflicts), ([], [gap], []))  # the gap is the decision that was made
        self.assertFalse((records / "pc-a/tickets").exists())
        replay = ShadowTicket.model_validate_json((records / "pc-a/replays/E-dd/company_tsmc/2026-11-23.json").read_text())
        self.assertEqual((batch.replays, replay.issuance, replay.issued_at), ([replay], "replay", later))
        self.assertEqual(batch.kept, [gap.gap_id])  # the official gap was found, not written by this run
        again = emit(root, folder, SESSION, records, now=datetime.combine(SESSION, time(23, 0), NEW_YORK), machine="pc-a", only=["TSM"])
        self.assertEqual((again.replays, again.gaps, again.reused, again.kept), ([replay], [gap], [replay.ticket_id], [gap.gap_id]))  # the same result: reused
        # An earlier reconstruction that said something else is not reported as this run's result: it is a conflict, and it is left alone.
        stored = records / "pc-a/replays/E-dd/company_tsmc/2026-11-23.json"
        other = json.loads(stored.read_text())
        other.update(score=0.87, seed_scores=[0.87, 0.87])
        stored.write_text(json.dumps(other))
        with self.assertRaisesRegex(SystemExit, "replays/E-dd/company_tsmc/2026-11-23.json: a replay is already there and differs"):
            emit(root, folder, SESSION, records, now=datetime.combine(SESSION, time(23, 30), NEW_YORK), machine="pc-a", only=["TSM"])
        self.assertEqual(json.loads(stored.read_text()), other)

    def test_no_ticket_is_written_for_a_session_before_a_subjects_registered_first_session(self):
        root, folder, git, _ = self.registered("starts_later", first="2026-11-24")
        git("add", "-A"); git("commit", "-q", "-m", "register")
        records = Path(self.temp.name) / "starts_later_records"
        batch = emit(root, folder, SESSION, records, now=EVENING, machine="pc-a")
        self.assertEqual((batch.tickets, batch.gaps), ([], []))
        self.assertEqual([p.name for p in records.rglob("*") if p.is_file()], [".lock"])
        # A gate can start later than its subject: QQQ's record starts on the 23rd, and its single-seed gate only on the 24th.
        own, own_folder, git3, registration = self.registered("gate_starts_later")
        registration["subjects"] = [{**s, "gate_first_session": {"W-dd": "2026-11-24"}} if s["subject"] == "QQQ" else s for s in registration["subjects"]]
        (own / REGISTRATION).write_text(json.dumps(registration, indent=1) + "\n")
        git3("add", "-A"); git3("commit", "-q", "-m", "register")
        written = emit(own, own_folder, SESSION, Path(self.temp.name) / "gate_starts_later_records", now=EVENING, machine="pc-a")
        self.assertEqual(sorted((t.subject_id, t.gate) for t in written.tickets), [("company:tsmc", "E-dd"), ("fund:qqq", "E-dd")])
        undated, _, git2, registration = self.registered("undated")
        registration["subjects"][0].pop("first_session")
        (undated / REGISTRATION).write_text(json.dumps(registration, indent=1) + "\n")
        git2("add", "-A"); git2("commit", "-q", "-m", "register")
        with self.assertRaisesRegex(SystemExit, "gives no first session for QQQ"):
            emit(undated, undated / "artifacts/snapshots" / str(SESSION), SESSION, records, now=EVENING, machine="pc-a")

    def test_a_second_run_for_the_same_issuer_stops_while_one_is_writing(self):
        import fcntl
        root, folder, git, _ = self.registered("locked")
        git("add", "-A"); git("commit", "-q", "-m", "register")
        records = Path(self.temp.name) / "locked_records"
        (records / "pc-a").mkdir(parents=True)
        with (records / "pc-a/.lock").open("w") as held:  # stands for a run in progress
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(SystemExit, "another run is writing records for pc-a"):
                emit(root, folder, SESSION, records, now=EVENING, machine="pc-a", only=["TSM"])
            self.assertEqual([p.name for p in (records / "pc-a").iterdir()], [".lock"])  # it wrote nothing, neither ticket nor gap
            self.assertEqual(len(emit(root, folder, SESSION, None, now=EVENING, machine="pc-a", dry_run=True, only=["TSM"]).tickets), 1)  # a rehearsal is not a writer
        self.assertEqual(len(emit(root, folder, SESSION, records, now=EVENING, machine="pc-a", only=["TSM"]).tickets), 1)  # once it is released

    def test_a_month_that_cannot_be_fitted_is_carried_like_a_missing_input_and_stops_nothing(self):
        unfittable = json.loads(json.dumps(self.registration))
        unfittable["parameters"]["min_positive"] = 10 ** 6  # no training fold can have that many positive labels
        path = Path(self.temp.name) / "unfittable.json"
        path.write_text(json.dumps(unfittable))
        records = Path(self.temp.name) / "unfittable_records"
        run = lambda: emit(self.root, self.folder, SESSION, records, now=EVENING, machine="test-machine", dry_run=True, registration_path=path)
        first = run()  # every input is present, and still there is no score: recorded as a gap with its reason, for every gate
        self.assertEqual((first.tickets, len(first.gaps), {g.missing_inputs for g in first.gaps}), ([], 3, {()}))
        self.assertTrue(all("could not be fitted" in g.reason for g in first.gaps))
        model = next(t for t in self.tickets if (t.subject_id, t.gate) == ("fund:qqq", "E-dd"))
        day = date(2026, 11, 20)
        earlier = {**json.loads(model.model_dump_json()), "session": str(day), "ticket_id": f"shadow:E-dd:fund:qqq:{day}", "state": "YELLOW", "target_exposure": 0.5,
                   "decision_time": datetime.combine(day, time(17, 0), NEW_YORK).isoformat(), "issued_at": datetime.combine(day, time(19, 0), NEW_YORK).isoformat()}
        folder = records / "test-machine/tickets/E-dd/fund_qqq"
        folder.mkdir(parents=True)
        (folder / f"{day}.json").write_text(ShadowTicket(**earlier).model_dump_json())
        second = run()  # one gate now has a live ticket to hold; the other two are still gaps, and all three were processed
        held = second.tickets[0]
        self.assertEqual((len(second.tickets), len(second.gaps), second.errors), (1, 2, []))
        self.assertEqual((held.state, held.held, held.no_score_reason, held.continues, all(v is not None for v in held.inputs.values())), ("YELLOW", True, "no_fit", "held", True))

    def test_the_repository_registration_is_a_well_formed_draft(self):
        r = json.loads((ROOT / REGISTRATION).read_text())
        self.assertTrue(r["status"].startswith("DRAFT") and r["code"]["commit"] is None and r["issuer"] is None)
        self.assertTrue(all("first_session" in s and s["first_session"] is None for s in r["subjects"]))  # set when it is registered
        self.assertEqual(sorted(r["pinned_files"]), sorted(COPIED))  # the files that decide which values a decision may read
        self.assertTrue(all((ROOT / name).is_file() for name in r["pinned_files"]))
        bound = {s["series_id"] for s in json.loads((ROOT / BINDINGS / "series_bindings.json").read_text())["series"]}
        base = r["inputs"]["base_factors"]
        factors = [Factor(**f) for f in base["shared"] + base["per_subject"]]
        self.assertLessEqual({i for f in factors for i in f.series_ids}, bound | {"subject:close"})  # every series is in the catalogue now
        defined = {f.factor_id for f in factors} | set(base["from_factor_definitions"])
        self.assertEqual(len(r["inputs"]["input_definitions"]), 33)
        for name, definition in r["inputs"]["input_definitions"].items():
            self.assertLessEqual(set(definition["of"]), defined, name)
            self.assertIn(definition["op"], r["inputs"]["composite_operations"], name)
        for subject in r["subjects"]:
            self.assertLessEqual({subject["open"], subject["close"]}, bound)
            self.assertLessEqual(set(subject["gates"]), set(r["gates"]))
            self.assertTrue("class_bounds" in subject or all(r["gates"][g]["target"] == "forward_drawdown" for g in subject["gates"]))
        self.assertEqual(r["gates"]["E-dd"]["seeds"], list(range(7)))


if __name__ == "__main__":
    unittest.main()
