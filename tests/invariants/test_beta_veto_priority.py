"""Invariant: a RED beta gate overrides every bullish single-stock ticket, and tickets are written once."""

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_pit_data", "quant_causal_graph", "quant_evaluator", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_contracts.forecast import EXPOSURE, AlphaTicket, BetaTicket, MaturedOutcome
    from quant_loop.tickets import apply_beta_veto, emit, mature, write_once
except ModuleNotFoundError:
    BetaTicket = None

WHEN = datetime(2026, 11, 20, 17, 0, tzinfo=ZoneInfo("America/New_York"))
SHA = "0" * 64
LEGACY = {"2026-10-23", "2026-11-20"}  # the two sessions ticketed, for each fund, before issuance was recorded


def beta(state, session=date(2026, 11, 20)):
    return BetaTicket(ticket_id=f"beta:fund:soxx:{session}", subject_id="fund:soxx", session=session, decision_time=WHEN,
                      issued_at=WHEN + timedelta(minutes=5), issuance="live", state=state,
                      target_exposure=EXPOSURE[state], held=False, stressed_rules=(), inputs={}, horizon_sessions=20,
                      source="test", recipe_sha256=SHA, snapshot_sha256={})


def alpha(direction, session=date(2026, 11, 20)):
    return AlphaTicket(ticket_id=f"alpha:company:tsmc:{session}", subject_id="company:tsmc", session=session, decision_time=WHEN,
                       direction=direction, horizon_sessions=5, recipe_sha256=SHA, snapshot_sha256={})


@unittest.skipIf(BetaTicket is None, "pydantic is not installed")
class BetaVetoPriorityTests(unittest.TestCase):
    def test_red_vetoes_every_bullish_ticket_and_nothing_in_the_ticket_can_override_it(self):
        self.assertEqual(apply_beta_veto(alpha("bullish"), beta("RED")).action, "vetoed")
        strong = alpha("bullish").model_copy(update={"horizon_sessions": 21, "ticket_id": "alpha:company:tsmc:urgent"})
        self.assertEqual(apply_beta_veto(strong, beta("RED")).action, "vetoed")
        self.assertEqual([apply_beta_veto(alpha(d), beta("RED")).action for d in ("bearish", "neutral")], ["allowed", "allowed"])

    def test_yellow_caps_and_green_allows(self):
        self.assertEqual([apply_beta_veto(alpha("bullish"), beta(s)).action for s in ("GREEN", "YELLOW", "RED")],
                         ["allowed", "capped", "vetoed"])
        result = apply_beta_veto(alpha("bullish"), beta("YELLOW"))
        self.assertEqual((result.alpha_ticket_id, result.beta_ticket_id), ("alpha:company:tsmc:2026-11-20", "beta:fund:soxx:2026-11-20"))

    def test_gate_from_another_session_cannot_clear_a_ticket(self):
        with self.assertRaisesRegex(ValueError, "different session"):
            apply_beta_veto(alpha("bullish"), beta("GREEN", date(2026, 11, 19)))

    def test_ticket_is_consistent_written_once_and_holds_no_outcome(self):
        with self.assertRaises(ValueError):
            BetaTicket.model_validate({**beta("RED").model_dump(), "target_exposure": 1.0})  # exposure must be the state's
        self.assertFalse({"forward_drawdown", "forward_average_return"} & set(BetaTicket.model_fields))
        self.assertIn("forward_drawdown", MaturedOutcome.model_fields)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "SOXX" / "2026-11-20.json"
            write_once(path, beta("RED").model_dump_json())
            with self.assertRaises(SystemExit):
                write_once(path, beta("GREEN").model_dump_json())
            self.assertEqual(BetaTicket.model_validate_json(path.read_text()).state, "RED")
            self.assertEqual([p.name for p in path.parent.iterdir()], ["2026-11-20.json"])  # no temporary file left

    def test_no_ticket_before_its_decision_time_or_for_a_session_that_has_not_happened(self):
        snapshot = ROOT / "artifacts/snapshots/2026-11-22"
        if not (snapshot / "SOXX.json").exists():
            self.skipTest("the raw price snapshot is not on this machine")
        day = date(2026, 11, 20)
        at = lambda hour, minute, d=day: datetime(d.year, d.month, d.day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
        with self.assertRaisesRegex(SystemExit, "before the decision time"):
            emit(ROOT, snapshot, "SOXX", day, now=at(16, 59))  # the session has closed, the decision time has not come
        live = emit(ROOT, snapshot, "SOXX", day, now=at(17, 1))
        self.assertEqual((live.issuance, live.issued_at, live.state), ("live", at(17, 1), "RED"))
        weekend = emit(ROOT, snapshot, "SOXX", day, now=at(12, 0, date(2026, 11, 22)))  # still before Monday's open
        self.assertEqual(weekend.issuance, "live")
        self.assertEqual(emit(ROOT, snapshot, "SOXX", day, now=at(9, 30, date(2026, 11, 23))).issuance, "replay")
        # Monday's ticket cannot be written on Sunday from Friday's values, nor at any time before Monday's close is in the data.
        for when in (at(12, 0, date(2026, 11, 22)), at(18, 0, date(2026, 11, 23))):
            with self.assertRaisesRegex(SystemExit, "no SOXX close for 2026-11-23"):
                emit(ROOT, snapshot, "SOXX", date(2026, 11, 23), now=when)
        with self.assertRaises(ValueError):
            BetaTicket.model_validate({**live.model_dump(), "issued_at": at(16, 0)})  # the contract refuses it too
        with self.assertRaisesRegex(ValueError, "together or not at all"):
            BetaTicket.model_validate({**live.model_dump(), "issuance": None})
        self.assertEqual({live.issuance, weekend.issuance}, {"live"})  # every ticket emit builds records its issuance

    def test_saved_tickets_follow_the_frozen_rule_and_keep_their_outcomes_apart(self):
        for symbol in ("SOXX", "QQQ"):
            folder = ROOT / "artifacts/tickets" / symbol
            frozen = {line.split(",")[0]: line.strip().split(",") for line in
                      (ROOT / f"artifacts/ablations/sector_risk_baseline/output/positions_{symbol}.csv").read_text().splitlines()[1:]}
            for path in sorted(folder.glob("*.json")):
                ticket = BetaTicket.model_validate_json(path.read_text())
                if path.stem in LEGACY:  # issued before tickets recorded their issuance: kept as written, unknown, not relabelled
                    self.assertEqual((ticket.issued_at, ticket.issuance), (None, None), path.name)
                else:  # every later ticket says when it was issued, and never before its decision
                    self.assertIn(ticket.issuance, ("live", "replay"), path.name)
                    self.assertGreaterEqual(ticket.issued_at, ticket.decision_time, path.name)
                    continue  # it need not be a date the frozen positions cover
                self.assertEqual(f"{ticket.target_exposure:.2f}", frozen[path.stem][8], path.name)  # the rule's own target that day
                self.assertEqual(len(ticket.stressed_rules), int(frozen[path.stem][7]))
            self.assertLessEqual(LEGACY, {p.stem for p in folder.glob("*.json")})  # the four originals are still there
            outcome = MaturedOutcome.model_validate_json((folder / "outcomes/2026-10-23.json").read_text())
            self.assertEqual((outcome.ticket_id.split(":")[-1], str(outcome.last_session)), ("2026-10-23", "2026-11-20"))
            for path in sorted((folder / "outcomes").glob("*.json")):  # outcomes accumulate; each belongs to a ticket and follows it
                scored = MaturedOutcome.model_validate_json(path.read_text())
                ticket = BetaTicket.model_validate_json((folder / path.name).read_text())
                self.assertEqual(scored.ticket_id, ticket.ticket_id)
                self.assertGreater(scored.entry_session, ticket.session)

    def test_ticket_is_not_scored_before_its_horizon_is_in_the_data(self):
        snapshot = ROOT / "artifacts/snapshots/2026-11-22"  # fixed: its prices end on 2026-11-20
        if not (snapshot / "SOXX.json").exists():
            self.skipTest("the raw price snapshot is not on this machine")
        with self.assertRaisesRegex(SystemExit, "has not matured: prices to 2026-12-21 are needed"):
            mature(ROOT, snapshot, "SOXX", date(2026, 11, 20))  # premature at this snapshot, whatever is scored later
        done = mature(ROOT, snapshot, "SOXX", date(2026, 10, 23))
        self.assertEqual(done.model_dump_json(indent=1) + "\n", (ROOT / "artifacts/tickets/SOXX/outcomes/2026-10-23.json").read_text())


if __name__ == "__main__":
    unittest.main()
