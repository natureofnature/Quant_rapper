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
sys.path.insert(0, str(ROOT / "packages/quant_contracts/src"))  # the ticket and gap schemas issuance uses
sys.path.insert(0, str(ROOT / "packages/quant_loop/src"))  # the rules for reading the records
import snapshot as snapshots
from record_state import Records, read_record, record_state

RULE = "artifacts/ablations/sector_risk_baseline/registration.json"
SHADOW = "artifacts/forward/shadow_gates/registration.json"
BINDINGS = "knowledge_base/prior_knowledge/bindings/series_bindings.json"


def expected(root: Path, base: Path) -> list[tuple[str, str, str]]:
    """(gate, subject folder, first session) for every decision the job is meant to record, each from its own start.

    The start never depends on what was written, or a subject that failed from the first day
    would never be expected. The rule's record starts, for every subject of its registration,
    with the first session this machine ran the job for. A shadow gate's starts on the date its
    registration gives: the gate's own for that subject if it has one, else the subject's.
    Adding a gate or a subject later therefore raises nothing about the sessions before it.
    """
    anchor = {s["series_id"]: s["anchors"][0].replace(":", "_") for s in json.loads((root / BINDINGS).read_text(encoding="utf-8"))["series"]}
    ran = sorted({p.name for p in base.glob("runs/*") if p.is_dir()} | {p.stem for kind in ("tickets", "gaps") for p in base.glob(f"{kind}/rule/*/*.json")})
    out = [("rule", anchor[s["close"]], ran[0]) for s in json.loads((root / RULE).read_text(encoding="utf-8"))["subjects"]] if ran else []
    shadow = json.loads((root / SHADOW).read_text(encoding="utf-8"))
    if not str(shadow.get("status", "")).startswith("DRAFT"):
        for s in shadow["subjects"]:
            for gate in s["gates"]:
                start = (s.get("gate_first_session") or {}).get(gate) or s.get("first_session")
                if start:
                    out.append((gate, anchor[s["close"]], start))
    return out


def why_missing(base: Path, session: date, gate: str) -> str:
    logs = sorted((base / "runs" / str(session)).glob("*.json")) if (base / "runs" / str(session)).is_dir() else []
    if not logs:
        return "the job did not run for this session"
    try:
        stages = json.loads(logs[-1].read_text(encoding="utf-8"))["stages"]
    except (ValueError, KeyError):
        return f"the run log {logs[-1].name} cannot be read"
    for name in ("snapshot", "records", "manifest", "rule" if gate == "rule" else "shadow"):  # in the order the job reaches them
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
    try:  # the same view of the records as the job has: a file that is not what was committed, or of which two versions exist, is no ticket
        _, changed, conflicts = record_state(records, machine)
        store = Records(records, machine)
    except OSError as error:  # without it a file on disk cannot be told from the record: nothing is reported as fine
        return [f"the records could not be verified, so no ticket is counted: {error}"]
    decisions = expected(root, base)
    # The scan starts where the first decision was due, which can be before this machine first ran: a session a gate was
    # registered for and nobody ran the job on is a missed one too. Each decision is then expected from its own date.
    starts = sorted({start for _, _, start in decisions} | set(recorded[:1]))
    first = since or (date.fromisoformat(starts[0]) if starts else None)
    if first is None:
        return [f"{machine} has recorded nothing yet"]
    lines = []
    for session in (s for s in sessions if first <= s <= last):
        for gate, subject, start in decisions:
            if str(session) < start:
                continue
            official = store.official(gate, subject, session)  # the one answer every reader of the records uses
            if official.stray is not None:
                lines.append(f"{session} {gate} {subject}: {official.stray.relative_to(records).as_posix()} is not counted; "
                             f"the committed record of this decision is the {official.kind}")
            if official.problem:
                lines.append(f"{session} {gate} {subject}: no ticket counted; {official.problem}"
                             + (f", {official.path.relative_to(records).as_posix()}" if official.path is not None else ""))
                continue
            found = official.path
            if found is None:
                lines.append(f"{session} {gate} {subject}: no ticket; {why_missing(base, session, gate)}")
                continue
            place, kind = found.relative_to(records).as_posix(), official.kind
            # What git already knows comes first: a file it flags is not parsed to be judged.
            if place in conflicts:
                lines.append(f"{session} {gate} {subject}: not a {kind} on record; an earlier run staged one and the file has changed since, so neither was published")
                continue
            if place in changed:
                lines.append(f"{session} {gate} {subject}: not the {kind} on record; the file differs from what was committed")
                continue
            record = read_record(found, base)  # the schema issuance uses, and the decision the place names
            if isinstance(record, str):
                lines.append(f"{session} {gate} {subject}: no ticket; {place} is not this decision's {kind}: {record}")
            elif kind == "gap":
                lines.append(f"{session} {gate} {subject}: a gap; {record.reason}")
            elif record.issuance != "live":
                lines.append(f"{session} {gate} {subject}: written late, at {record.issued_at}; it is a replay and not part of the forward record")
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
