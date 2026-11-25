"""Which decisions of the forward record have no ticket written in time, and why.

    python apps/cli/missed_runs.py --records ../Quant_rapper_runtime_data
    python apps/cli/missed_runs.py --records ../Quant_rapper_runtime_data --since 2026-11-23 --machine other-pc

For every session from the first one this machine recorded (or --since) to the latest one that
has closed, and for every decision expected (the frozen rule for each of its subjects, and each
registered shadow gate once its registration is registered), it reports the ones that are not a
live ticket: written late, recorded as a gap, or not there at all, with what the run log says.
It reads the records only and changes nothing. It exits 1 when anything is reported, so a
scheduler can raise it the next morning.
"""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/cli"))
import snapshot as snapshots

RULE = "artifacts/ablations/sector_risk_baseline/registration.json"
SHADOW = "artifacts/forward/shadow_gates/registration.json"
BINDINGS = "knowledge_base/prior_knowledge/bindings/series_bindings.json"


def expected(root: Path) -> list[tuple[str, str]]:
    """(gate, subject folder) for every decision the job is meant to record each session."""
    anchor = {s["series_id"]: s["anchors"][0].replace(":", "_") for s in json.loads((root / BINDINGS).read_text(encoding="utf-8"))["series"]}
    out = [("rule", anchor[s["close"]]) for s in json.loads((root / RULE).read_text(encoding="utf-8"))["subjects"]]
    shadow = json.loads((root / SHADOW).read_text(encoding="utf-8"))
    if not str(shadow.get("status", "")).startswith("DRAFT"):
        out += [(gate, anchor[s["close"]]) for s in shadow["subjects"] for gate in s["gates"]]
    return out


def why_missing(base: Path, session: date, gate: str) -> str:
    logs = sorted((base / "runs" / str(session)).glob("*.json")) if (base / "runs" / str(session)).is_dir() else []
    if not logs:
        return "the job did not run for this session"
    stages = json.loads(logs[-1].read_text(encoding="utf-8"))["stages"]
    for name in ("snapshot", "rule" if gate == "rule" else "shadow"):
        stage = stages.get(name, {})
        if stage.get("failed"):
            return f"{name}: {stage['failed'] if isinstance(stage['failed'], str) else json.dumps(stage['failed'])}"
        if stage.get("skipped"):
            return stage["skipped"]
    return "the job ran and logged no failure for this stage"


def report(root: Path, records: Path, machine: str, since: date | None, now: datetime) -> list[str]:
    base = records / machine
    sessions = [date.fromisoformat(s) for s in json.loads((root / snapshots.CALENDAR).read_text(encoding="utf-8"))["sessions"]]
    last = snapshots.latest_closed_session(sessions, now)
    recorded = sorted({p.stem for p in base.glob("tickets/*/*/*.json")} | {p.name for p in base.glob("runs/*") if p.is_dir()})
    first = since or (date.fromisoformat(recorded[0]) if recorded else None)
    if first is None:
        return [f"{machine} has recorded nothing yet"]
    lines = []
    for session in (s for s in sessions if first <= s <= last):
        for gate, subject in expected(root):
            ticket, gap = (base / kind / gate / subject / f"{session}.json" for kind in ("tickets", "gaps"))
            if ticket.is_file():
                written = json.loads(ticket.read_text(encoding="utf-8"))
                if written.get("issuance") != "live":
                    lines.append(f"{session} {gate} {subject}: written late, at {written.get('issued_at')}; it is a replay and not part of the forward record")
            elif gap.is_file():
                lines.append(f"{session} {gate} {subject}: a gap; {json.loads(gap.read_text(encoding='utf-8'))['reason']}")
            else:
                lines.append(f"{session} {gate} {subject}: no ticket; {why_missing(base, session, gate)}")
    return lines


def main(arguments: list[str] | None = None, root: Path = ROOT, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report the decisions that have no ticket written in time.")
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--machine", default=socket.gethostname(), help="default: this computer")
    parser.add_argument("--since", type=date.fromisoformat, help="default: the first session this machine recorded")
    args = parser.parse_args(arguments)
    lines = report(root, args.records.resolve(), args.machine, args.since, now or datetime.now(timezone.utc))
    print("\n".join(lines) if lines else f"{args.machine}: every expected decision has a live ticket")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
