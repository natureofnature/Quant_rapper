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
    from quant_contracts.forecast import ShadowTicket
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


def small(registration: dict) -> dict:
    """The repository's registration with six inputs and a short history, so that a test fits in seconds."""
    r = json.loads(json.dumps(registration))
    r["inputs"]["input_definitions"] = {k: v for k, v in r["inputs"]["input_definitions"].items() if k in KEPT}
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
        assert snapshot.main(["--no-backup"], root=cls.root, fetcher=cls.fetcher, pause=0.0, now=EVENING) == 0
        cls.folder = cls.root / "artifacts/snapshots" / str(SESSION)
        cls.tickets, cls.digest = emit(cls.root, cls.folder, SESSION, None, now=EVENING, machine="test-machine", dry_run=True)

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
            return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": stamps, "indicators": {"quote": [{"open": [c or 1.0 for c in closes], "close": closes}]}}]}}).encode()
        if "_History.csv" in url:
            name = url.rsplit("/", 1)[1]
            levels = 20.0 * walk(name, len(cls.equity), 0.05)
            return ("DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(f"{d:%m/%d/%Y},{v:.4f},{v:.4f},{v:.4f},{v:.4f}\n" for d, v in zip(cls.equity, levels))).encode()
        year = int(url.split("/all")[0].rsplit("/", 1)[1])
        days = [d for d in cls.bond_days if d.year == year and (START <= d <= SESSION if year >= START.year else d == min(x for x in cls.bond_days if x.year == year))]
        values = dict(zip([d for d in cls.bond_days if START <= d <= SESSION], 2.0 + np.cumsum(np.random.default_rng(7).normal(0.0, 0.03, 2000))))
        return ('Date,"5 YR","10 YR"\n' + "".join(f"{d:%m/%d/%Y},1.00,{values.get(d, 2.0):.2f}\n" for d in reversed(days))).encode()

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
        self.assertNotIn("GCF.json", qqq["E-dd"].inputs_sha256)  # a file these six inputs never read is not required

    def test_the_same_snapshot_gives_the_same_scores_and_a_draft_is_never_written(self):
        again, digest = emit(self.root, self.folder, SESSION, None, now=EVENING, machine="test-machine", dry_run=True)
        self.assertEqual((again, digest), (self.tickets, self.digest))  # the digest is what two machines compare
        records = Path(self.temp.name) / "records"
        with self.assertRaisesRegex(SystemExit, "the registration is a draft"):
            emit(self.root, self.folder, SESSION, records, now=EVENING, machine="test-machine")
        self.assertFalse(records.exists())

    def test_no_ticket_is_written_before_the_decision_time(self):
        with self.assertRaisesRegex(SystemExit, "before the decision time"):
            emit(self.root, self.folder, SESSION, None, now=datetime.combine(SESSION, time(16, 59), NEW_YORK), dry_run=True)
        late, _ = emit(self.root, self.folder, SESSION, None, now=datetime(2026, 11, 24, 9, 30, tzinfo=NEW_YORK), dry_run=True, only=["TSM"])
        self.assertEqual([t.issuance for t in late], ["replay"])  # at the next open it is no longer live

    def test_a_subject_whose_close_arrived_after_the_cutoff_gets_no_ticket_and_the_others_do(self):
        type(self).late_close = ("TSM", datetime.combine(SESSION, time(17, 30), NEW_YORK))
        try:
            root = Path(self.temp.name) / "late"
            shutil.copytree(self.root, root, ignore=shutil.ignore_patterns("snapshots"))
            for path in snapshot.SHARED:
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / path, root / path)
            self.assertEqual(snapshot.main(["--no-backup"], root=root, fetcher=self.fetcher, pause=0.0, now=EVENING), 0)
        finally:
            type(self).late_close = None
        tickets, _ = emit(root, root / "artifacts/snapshots" / str(SESSION), SESSION, None, now=EVENING, dry_run=True)
        self.assertEqual({t.subject_id for t in tickets}, {"fund:qqq"})  # TSM's close was stamped 17:30: nothing stands in for it

    def test_a_missing_input_holds_the_previous_state_and_records_no_score(self):
        root = Path(self.temp.name) / "short_calendar"
        shutil.copytree(self.root, root, ignore=shutil.ignore_patterns("snapshots"))
        for path in snapshot.SHARED:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / path, root / path)
        calendar = json.loads((root / snapshot.SHARED[0]).read_text())  # the calendar now ends before the next FOMC meeting, on 9 December
        calendar.update(last="2026-11-30", sessions=[s for s in calendar["sessions"] if s <= "2026-11-30"])
        (root / snapshot.SHARED[0]).write_text(json.dumps(calendar))
        self.assertEqual(snapshot.main(["--no-backup"], root=root, fetcher=self.fetcher, pause=0.0, now=EVENING), 0)
        tickets, _ = emit(root, root / "artifacts/snapshots" / str(SESSION), SESSION, None, now=EVENING, dry_run=True, only=["TSM"])
        ticket = tickets[0]
        self.assertEqual((ticket.held, ticket.score, ticket.seed_scores, ticket.inputs["sessions_to_fomc"]), (True, None, (), None))
        self.assertIsNotNone(ticket.inputs["vix_log"])

    def test_a_registered_ticket_is_written_once_by_the_pinned_code_only(self):
        root, records = Path(self.temp.name) / "registered", Path(self.temp.name) / "written"
        shutil.copytree(self.root, root)
        git = lambda *a: subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *a], check=True, capture_output=True).stdout.decode().strip()
        (root / "packages").mkdir()
        (root / "packages/code.py").write_text("VERSION = 1\n")
        (root / ".gitignore").write_text("artifacts/snapshots/2026-11-23/\n")
        git("init", "-q"); git("add", "-A"); git("commit", "-q", "-m", "code")
        commit = git("rev-parse", "HEAD")
        registered = {**self.registration, "status": "Registered for this test", "code": {"commit": commit}}
        (root / REGISTRATION).write_text(json.dumps(registered, indent=1) + "\n")
        folder = root / "artifacts/snapshots" / str(SESSION)
        with self.assertRaisesRegex(SystemExit, "must be the committed file"):
            emit(root, folder, SESSION, records, now=EVENING, machine="pc-a", only=["TSM"])
        git("add", "-A"); git("commit", "-q", "-m", "register")
        (root / "packages/code.py").write_text("VERSION = 2\n")
        with self.assertRaisesRegex(SystemExit, "the code differs from the registered commit"):
            emit(root, folder, SESSION, records, now=EVENING, machine="pc-a", only=["TSM"])
        (root / "packages/code.py").write_text("VERSION = 1\n")
        tickets, _ = emit(root, folder, SESSION, records, now=EVENING, machine="pc-a", only=["TSM"])
        path = records / "pc-a/tickets/E-dd/company_tsmc/2026-11-23.json"
        written = ShadowTicket.model_validate_json(path.read_text())
        self.assertEqual((written, written.code_commit, written.registration_sha256), (tickets[0], commit, hashlib.sha256((root / REGISTRATION).read_bytes()).hexdigest()))
        self.assertEqual(written.score, next(t for t in self.tickets if t.subject_id == "company:tsmc").score)  # as the rehearsal computed it
        with self.assertRaisesRegex(SystemExit, "written once"):
            emit(root, folder, SESSION, records, now=EVENING, machine="pc-a", only=["TSM"])
        emit(root, folder, SESSION, records, now=EVENING, machine="pc-b", only=["TSM"])  # another machine writes under its own name
        self.assertTrue((records / "pc-b/tickets/E-dd/company_tsmc/2026-11-23.json").is_file())

    def test_the_repository_registration_is_a_well_formed_draft(self):
        r = json.loads((ROOT / REGISTRATION).read_text())
        self.assertTrue(r["status"].startswith("DRAFT") and r["code"]["commit"] is None)
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
