"""Shadow tickets: what each registered fitted gate says after a session, written beside the rule's ticket.

    python -m quant_loop.shadow emit --session 2026-11-24 --snapshot artifacts/snapshots/2026-11-24 --records ../Quant_rapper_runtime_data
    python -m quant_loop.shadow emit ... --dry-run     compute and print, write nothing

A shadow ticket decides nothing. Everything comes from the registration: subjects, gates,
inputs, model and thresholds. The inputs are built from one snapshot through the bindings
catalogue, each as knowable before the decision time on its session. The model is refitted
on the whole history at every run, as the walk-forward does, so the score of the session
is the one the registered procedure gives for that snapshot.

Writing is refused while the registration is a draft, on any machine but the one it names as
issuer, unless the code is the commit it pins, and unless the bindings, factor definitions,
event schedule and calendars are the files it pins. A ticket goes to
<records>/<machine>/tickets/<gate>/<subject>/<session>.json, once. A run that finds some of a
session's tickets already written keeps those that match what it computes, writes the rest, and
reports any that differ without replacing them.

On a session with a score, the state is the registered procedure applied to that session's
snapshot. On a session without one, because an input was missing, the ticket carries the state
of the last live ticket of the same gate and subject; if there is none, a gap is recorded in
place of a ticket. Whether a ticket is live is decided by the clock at the moment it is written.

``--dry-run`` checks nothing about the registration and writes nothing, for rehearsal and for
comparing two machines: it prints a digest of every score, which must be equal on both for the
same snapshot.
"""

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
from zoneinfo import ZoneInfo

import numpy as np

from quant_contracts.forecast import EXPOSURE, ShadowGap, ShadowTicket
from quant_contracts.pit import SeriesBinding
from quant_evaluator.beta.forward_target import class_thresholds, forward_average_return, trailing_volatility
from quant_evaluator.beta.model_gate import gate_states, walk_forward_binary
from quant_evaluator.beta.risk_gate import forward_drawdown
from quant_loop.tickets import write_once
from quant_loop.wide_inputs import BINDINGS, build

REGISTRATION = "artifacts/forward/shadow_gates/registration.json"
STATES = ("GREEN", "YELLOW", "RED")
PINNED_PATHS = ("packages", "apps/cli")  # the code a registered ticket may be written by


def pinned_differences(root: Path, registration: dict) -> list[str]:
    """The files the registration pins that are missing or differ. They decide which values a decision may read."""
    wrong = []
    for name, expected in registration.get("pinned_files", {}).items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            wrong.append(name)
    return wrong


def refuse_unless_registered(root: Path, registration: dict, path: Path, machine: str) -> str:
    """The registered code commit, after checking that this is the registered experiment: its file, code, configuration and issuer."""
    if str(registration.get("status", "")).startswith("DRAFT"):
        raise SystemExit("the registration is a draft: no ticket is written under it (use --dry-run to rehearse)")
    commit = (registration.get("code") or {}).get("commit") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit("the registration pins no code commit")
    if not registration.get("issuer"):
        raise SystemExit("the registration names no issuing machine")
    if machine != registration["issuer"]:
        raise SystemExit(f"this machine is {machine}, and the registered issuer is {registration['issuer']}: only the issuer writes tickets (use --dry-run to compare scores)")
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True)
    if git("diff", "--quiet", "HEAD", "--", str(path.relative_to(root))).returncode or not git("ls-files", "--", str(path.relative_to(root))).stdout.strip():
        raise SystemExit("the registration must be the committed file, unchanged")
    if git("diff", "--quiet", commit, "--", *PINNED_PATHS).returncode:
        raise SystemExit(f"the code differs from the registered commit {commit[:7]}: no ticket is written by other code")
    if not registration.get("pinned_files"):
        raise SystemExit("the registration pins no bindings, definitions, schedule or calendars")
    wrong = pinned_differences(root, registration)
    if wrong:
        raise SystemExit(f"these files differ from the ones the registration pins: {', '.join(wrong)}. They decide which values a decision "
                         "may read, so no ticket is written until the registration is amended")
    return commit


@dataclass
class Batch:
    """What one run produced for a session. ``kept`` are tickets found already written and equal to what this run computed."""

    tickets: list[ShadowTicket] = field(default_factory=list)
    gaps: list[ShadowGap] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    digest: str = ""


def last_live_ticket(records: Path, machine: str, gate: str, subject: str, session: date) -> ShadowTicket | None:
    """The latest ticket of this issuer, gate and subject from before ``session`` that was written in time."""
    folder = records / machine / "tickets" / gate / subject
    for path in sorted((p for p in folder.glob("*.json") if p.stem < str(session)), reverse=True) if folder.is_dir() else []:
        ticket = ShadowTicket.model_validate_json(path.read_text(encoding="utf-8"))
        if ticket.issuance == "live":
            return ticket
    return None


SAME = ("gate", "subject_id", "session", "decision_time", "machine", "registration_id", "registration_sha256", "code_commit", "inputs_sha256")


def publish(path: Path, record, model, compare: tuple[str, ...], batch: Batch) -> object:
    """Write ``record`` once. One already there is kept if it is the same decision, and reported, never replaced, if it is not."""
    if path.is_file():
        existing = model.model_validate_json(path.read_text(encoding="utf-8"))
        if all(getattr(existing, k) == getattr(record, k) for k in compare):
            batch.kept.append(path.name if not hasattr(existing, "ticket_id") else existing.ticket_id)
            return existing  # with the time it was first written
        batch.conflicts.append(f"{path}: a record is already there and differs from what this run computed")
        return existing
    write_once(path, record.model_dump_json(indent=1) + "\n")
    return record


def scores_of(registration: dict, inputs, subject: dict) -> dict[str, tuple[list[float | None], list[list[float | None]]]]:
    """For each of the subject's gates: (the score per session, the per-seed scores behind it)."""
    P, name = registration["parameters"], subject["subject"]
    sessions, x = inputs.sessions, inputs.matrices[name]
    o, c, _ = inputs.prices[name]
    H = P["horizon_sessions"]
    complete = np.isfinite(x).all(axis=1)
    if not complete.any():
        raise SystemExit(f"{name}: no session has every input")
    first = sessions[int(np.argmax(complete))]
    average = [forward_average_return(o, c, i, H) for i in range(len(sessions))]
    drawdowns = [forward_drawdown(o, c, i, H) if average[i] is not None else None for i in range(len(sessions))]
    fitted: dict[tuple[str, int], list[float | None]] = {}

    def fit(target: str, seed: int) -> list[float | None]:
        if (target, seed) not in fitted:
            tree = {**P["tree"], "seed": seed}
            if target == "forward_drawdown":  # the label is cut inside each fold, from that fold's own training outcomes
                outcomes, share = drawdowns, P["worst_share"]
            else:
                floor, cap = subject["class_bounds"]
                outcomes, share = [], None
                for i, value in enumerate(average):
                    volatility = trailing_volatility(c, i, P["volatility_sessions"])
                    outcomes.append(None if value is None or volatility is None else float(value < class_thresholds(volatility, floor, cap, H)[0]))
            fitted[(target, seed)] = walk_forward_binary(sessions, x, outcomes, first, tree, share, H, 0, P["min_train"], P["min_positive"])
        return fitted[(target, seed)]

    out = {}
    for gate in subject["gates"]:
        spec = registration["gates"][gate]
        by_seed = [fit(spec["target"], seed) for seed in spec["seeds"]]
        mean = [None if any(s[i] is None for s in by_seed) else float(np.mean([s[i] for s in by_seed])) for i in range(len(sessions))]
        out[gate] = (mean, by_seed)
    return out


def emit(root: Path, snapshot: Path, session: date, records: Path | None, now: datetime | None = None, machine: str | None = None,
         dry_run: bool = False, only: list[str] | None = None, registration_path: Path | None = None, clock=None) -> Batch:
    """Every registered shadow ticket for ``session``, written to ``records`` unless ``dry_run``.

    ``clock`` gives the current time and is read again immediately before each ticket is
    written: that reading is the ticket's issuance time and decides whether it is live.
    ``now`` fixes the clock, for tests. A subject whose close for the session was not knowable
    before the decision time gets no ticket: an earlier day's values never stand in for a
    session that has not been read. A gate that has no state yet gets none either.
    """
    path = registration_path or root / REGISTRATION
    registration = json.loads(path.read_text(encoding="utf-8"))
    machine = machine or socket.gethostname()
    clock = clock or ((lambda: now) if now is not None else (lambda: datetime.now(timezone.utc)))
    commit = "0" * 40 if dry_run else refuse_unless_registered(root, registration, path, machine)
    if dry_run and pinned_differences(root, registration):
        print(f"note: these files differ from the ones the registration pins: {', '.join(pinned_differences(root, registration))}")
    P, G, spec = registration["parameters"], registration["parameters"]["gate"], registration["inputs"]
    subjects = [s for s in registration["subjects"] if only is None or s["subject"] in only]
    zone = ZoneInfo("America/New_York")
    cutoff = datetime.combine(session, time.fromisoformat(spec["decision_time"]), zone)
    if clock() < cutoff:
        raise SystemExit(f"it is before the decision time {cutoff.isoformat()}: no ticket is written early")
    inputs = build(spec, root, snapshot, {s["subject"]: (s["open"], s["close"]) for s in subjects}, session)
    catalogue = {s["series_id"]: SeriesBinding(**s) for s in json.loads((root / BINDINGS / "series_bindings.json").read_text(encoding="utf-8"))["series"]}
    later = [s for s in inputs.equity.sessions if s > session]
    registration_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    batch, record = Batch(), {}
    for subject in subjects:
        name = subject["subject"]
        closes = inputs.prices[name][2]
        if session not in closes or closes[session].available_at >= cutoff:
            print(f"{name}: its close for {session} was not knowable before {cutoff.time()}: no ticket")
            continue
        next_open = datetime.combine(later[0], catalogue[subject["open"]].value_time, zone) if later else None
        subject_id = catalogue[subject["close"]].anchors[0]
        folder = subject_id.replace(":", "_")
        row = {n: (None if not np.isfinite(v) else float(v)) for n, v in zip(inputs.names, inputs.matrices[name][-1])}
        common = dict(gate=None, subject_id=subject_id, session=session, decision_time=cutoff, machine=machine,
                      registration_id=registration["registration_id"], registration_sha256=registration_hash, code_commit=commit, inputs_sha256=inputs.hashes[name])
        for gate, (mean, by_seed) in scores_of(registration, inputs, subject).items():
            record[f"{name}:{gate}"] = [[None if v is None else repr(v) for v in seed] for seed in by_seed]
            common["gate"] = gate
            if mean[-1] is None:  # no score: the state is not recomputed, it is the last one this issuer actually published in time
                previous = None if records is None else last_live_ticket(records, machine, gate, folder, session)
                if previous is None:
                    gap = ShadowGap(gap_id=f"gap:{gate}:{subject_id}:{session}", issued_at=clock(), missing_inputs=tuple(n for n, v in row.items() if v is None),
                                    reason="no score for this session and no earlier live ticket of this gate and subject to hold", **common)
                    if not dry_run:
                        gap = publish(records / machine / "gaps" / gate / folder / f"{session}.json", gap, ShadowGap, SAME + ("missing_inputs",), batch)
                    batch.gaps.append(gap)
                    continue
                state, held = previous.state, True
            else:
                index, held = gate_states(mean, G["red_quantile"], G["yellow_quantile"], G["window"], G["min_scores"], G["step_down_after"])[-1][0], False
                if index is None:
                    print(f"{name} {gate}: the gate has no state yet: no ticket")
                    continue
                state = STATES[index]
            issued = clock()  # read now, with the work done: this is when the ticket exists
            ticket = ShadowTicket(ticket_id=f"shadow:{gate}:{subject_id}:{session}", issued_at=issued,
                                  issuance="live" if next_open is not None and issued < next_open else "replay",
                                  state=state, target_exposure=EXPOSURE[state], held=held, score=mean[-1],
                                  seed_scores=() if mean[-1] is None else tuple(s[-1] for s in by_seed), model_month=f"{session:%Y-%m}",
                                  inputs=row, horizon_sessions=P["horizon_sessions"], **common)
            if not dry_run:
                ticket = publish(records / machine / "tickets" / gate / folder / f"{session}.json", ticket, ShadowTicket,
                                 SAME + ("state", "held", "score", "seed_scores", "inputs", "model_month"), batch)
            batch.tickets.append(ticket)
    batch.digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
    if batch.conflicts:
        raise SystemExit("the batch was written except for records that differ from ones already there, which were left alone:\n  " + "\n  ".join(batch.conflicts))
    return batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the registered shadow tickets for one session.")
    parser.add_argument("action", choices=("emit",))
    parser.add_argument("--session", required=True, type=date.fromisoformat)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--records", type=Path, help="the folder of the records repository; required unless --dry-run")
    parser.add_argument("--subject", action="append", help="only this subject; may be given more than once")
    parser.add_argument("--dry-run", action="store_true", help="compute and print; check nothing about the registration and write nothing")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    if not args.dry_run and args.records is None:
        raise SystemExit("--records is required to write tickets")
    batch = emit(args.root, args.snapshot.resolve(), args.session, args.records, dry_run=args.dry_run, only=args.subject)
    for t in batch.tickets:
        print(f"{t.ticket_id}: {t.state}, exposure {t.target_exposure}, score {None if t.score is None else round(t.score, 4)}, held {t.held}, {t.issuance}"
              + (" (already written, kept)" if t.ticket_id in batch.kept else ""))
    for g in batch.gaps:
        print(f"{g.gap_id}: no ticket; missing {list(g.missing_inputs)}")
    print(f"{'dry run, nothing written' if args.dry_run else f'{len(batch.tickets) - len(batch.kept)} tickets written, {len(batch.kept)} kept'}; scores digest {batch.digest}")


if __name__ == "__main__":
    main()
