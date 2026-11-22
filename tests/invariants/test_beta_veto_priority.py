"""Invariant: a RED beta gate overrides every bullish single-stock ticket, and tickets are written once."""

from datetime import date, datetime
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
    from quant_loop.tickets import apply_beta_veto, write_once
except ModuleNotFoundError:
    BetaTicket = None

WHEN = datetime(2026, 11, 20, 17, 0, tzinfo=ZoneInfo("America/New_York"))
SHA = "0" * 64


def beta(state, session=date(2026, 11, 20)):
    return BetaTicket(ticket_id=f"beta:fund:soxx:{session}", subject_id="fund:soxx", session=session, decision_time=WHEN, state=state,
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

    def test_saved_tickets_follow_the_frozen_rule_and_keep_their_outcomes_apart(self):
        for symbol in ("SOXX", "QQQ"):
            folder = ROOT / "artifacts/tickets" / symbol
            frozen = {line.split(",")[0]: line.strip().split(",") for line in
                      (ROOT / f"artifacts/ablations/sector_risk_baseline/output/positions_{symbol}.csv").read_text().splitlines()[1:]}
            for path in sorted(folder.glob("*.json")):
                ticket = BetaTicket.model_validate_json(path.read_text())
                self.assertEqual(f"{ticket.target_exposure:.2f}", frozen[path.stem][8], path.name)  # the rule's own target that day
                self.assertEqual(len(ticket.stressed_rules), int(frozen[path.stem][7]))
            outcome = MaturedOutcome.model_validate_json((folder / "outcomes/2026-10-23.json").read_text())
            self.assertEqual((outcome.ticket_id.split(":")[-1], str(outcome.last_session)), ("2026-10-23", "2026-11-20"))
            self.assertFalse((folder / "outcomes/2026-11-20.json").exists())  # not matured: no outcome is written


if __name__ == "__main__":
    unittest.main()
