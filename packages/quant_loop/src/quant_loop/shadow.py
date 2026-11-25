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

The gate's state follows the record of what was issued. A session's own level is its score
against quantiles of the earlier scores in that session's snapshot. That level is then applied
to the state and step-down run of the live ticket of the session before. After a session with
no score, no ticket or a late ticket, the last live ticket's state is kept and the run starts
again, so a value that turns up in a later download cannot undo a reset that was issued. A
gate's first ticket takes its state from the registered procedure on that snapshot's history.
On a session without a score the ticket carries the last live ticket's state; with no such
ticket a gap is recorded in place of a ticket. Whether a ticket is live is decided by the clock
at the moment it is written.

One decision has one official record, a ticket or a gap, whichever was written first. A later
run that computes something else for it writes that under replays/, marked as a replay. Only
one run at a time writes for an issuer: a second one stops, so two runs cannot each publish an
outcome for the same decision.

A session can lack a score for two reasons, and the ticket says which: an input was not
knowable at the cutoff, or the month's model could not be fitted. The carry rule is the same
for both. A gate that fails does not stop the others; its error is reported when the rest is done.

``--dry-run`` checks nothing about the registration and writes nothing, for rehearsal and for
comparing two machines: it prints a digest of every score, which must be equal on both for the
same snapshot.
"""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
import fcntl
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
from quant_evaluator.beta.model_gate import gate_position, score_level, step, walk_forward_binary
from quant_evaluator.beta.risk_gate import forward_drawdown
from quant_loop.tickets import write_once
from quant_loop.wide_inputs import BINDINGS, build

REGISTRATION = "artifacts/forward/shadow_gates/registration.json"
STATES = ("GREEN", "YELLOW", "RED")
PINNED_PATHS = ("packages", "apps/cli")  # the code a registered ticket may be written by


def first_session_of(subject: dict, gate: str) -> str | None:
    """The first session a gate's record covers for a subject: the gate's own date if the registration gives one, else the subject's."""
    return (subject.get("gate_first_session") or {}).get(gate) or subject.get("first_session")


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
    undated = [s["subject"] for s in registration["subjects"] if not s.get("first_session")]
    if undated:
        raise SystemExit(f"the registration gives no first session for {', '.join(undated)}: each subject's forward record starts on a stated date")
    if not registration.get("pinned_files"):
        raise SystemExit("the registration pins no bindings, definitions, schedule or calendars")
    wrong = pinned_differences(root, registration)
    if wrong:
        raise SystemExit(f"these files differ from the ones the registration pins: {', '.join(wrong)}. They decide which values a decision "
                         "may read, so no ticket is written until the registration is amended")
    return commit


@dataclass
class Batch:
    """What one run produced for a session.

    ``tickets`` and ``gaps`` are the official records of the session's decisions after the run,
    whether this run wrote them or found them. ``kept`` names the ones it found already
    written and equal to what it computed. ``replays`` are results this run computed for a
    decision whose official record was already something else.
    """

    tickets: list[ShadowTicket] = field(default_factory=list)
    gaps: list[ShadowGap] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    replays: list[ShadowTicket] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)  # replays found already written and equal to what this run computed
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # a gate that could not be processed; the others were
    digest: str = ""


def last_live_ticket(records: Path, machine: str, gate: str, subject: str, session: date) -> tuple[ShadowTicket, str] | None:
    """The latest ticket of this issuer, gate and subject from before ``session`` that was written in time, with its file's hash."""
    folder = records / machine / "tickets" / gate / subject
    for path in sorted((p for p in folder.glob("*.json") if p.stem < str(session)), reverse=True) if folder.is_dir() else []:
        data = path.read_bytes()
        ticket = ShadowTicket.model_validate_json(data)
        if ticket.issuance == "live":
            return ticket, hashlib.sha256(data).hexdigest()
    return None


SAME = ("gate", "subject_id", "session", "decision_time", "machine", "registration_id", "registration_sha256", "code_commit", "inputs_sha256")
SAME_TICKET = SAME + ("state", "held", "no_score_reason", "score", "seed_scores", "inputs", "model_month", "continues", "previous_ticket_sha256", "level", "run")


def publish(records: Path, machine: str, gate: str, subject: str, session: date, record, batch: Batch):
    """Make ``record`` the official record of its decision unless one exists; returns the official record.

    A decision has one official record, a ticket or a gap, and the first one written stands.
    An equal one found there is kept with the time it was first written. If a gap stands and
    this run computed a ticket, the ticket is written under replays/ as a replay. Anything
    else that differs is reported and nothing is replaced.
    """
    base = records / machine
    ticket_path, gap_path = (base / kind / gate / subject / f"{session}.json" for kind in ("tickets", "gaps"))
    is_ticket = isinstance(record, ShadowTicket)
    if ticket_path.is_file():
        existing = ShadowTicket.model_validate_json(ticket_path.read_text(encoding="utf-8"))
        if is_ticket and all(getattr(existing, k) == getattr(record, k) for k in SAME_TICKET):
            batch.kept.append(existing.ticket_id)
        else:
            batch.conflicts.append(f"{ticket_path}: a ticket is already there and differs from what this run computed")
        return existing
    if gap_path.is_file():
        existing = ShadowGap.model_validate_json(gap_path.read_text(encoding="utf-8"))
        if not is_ticket:
            if all(getattr(existing, k) == getattr(record, k) for k in SAME + ("missing_inputs", "reason")):
                batch.kept.append(existing.gap_id)
            else:
                batch.conflicts.append(f"{gap_path}: a gap is already there and differs from what this run computed")
            return existing
        # The gap was the decision made in time and stays the official record. What can be computed now is a reconstruction.
        batch.kept.append(existing.gap_id)
        replay = record.model_copy(update={"issuance": "replay"})
        path = base / "replays" / gate / subject / f"{session}.json"
        if not path.is_file():
            write_once(path, replay.model_dump_json(indent=1) + "\n")
            batch.replays.append(replay)
        else:
            stored = ShadowTicket.model_validate_json(path.read_text(encoding="utf-8"))
            if all(getattr(stored, k) == getattr(replay, k) for k in SAME_TICKET):
                batch.reused.append(stored.ticket_id)
                batch.replays.append(stored)
            else:  # an earlier reconstruction said something else: neither is reported as the result
                batch.conflicts.append(f"{path}: a replay is already there and differs from what this run computed")
        return existing
    write_once(ticket_path if is_ticket else gap_path, record.model_dump_json(indent=1) + "\n")
    return record


@contextmanager
def only_run(records: Path, machine: str):
    """Hold the issuer's lock for the whole run, or stop: two runs must not each decide what a session's official record is."""
    folder = records / machine
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another run is writing records for {machine}: this one stops and writes nothing")
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def scores_of(registration: dict, inputs, subject: dict) -> dict[str, tuple[list[float | None], list[list[float | None]]]]:
    """For each of the subject's gates: (the score per session, the per-seed scores behind it)."""
    P, name = registration["parameters"], subject["subject"]
    sessions, x = inputs.sessions, inputs.matrices[name]
    o, c, _ = inputs.prices[name]
    H = P["horizon_sessions"]
    complete = np.isfinite(x).all(axis=1)
    if not complete.any():
        # No session has every input, so nothing can be fitted or scored. That is this subject's missing score, not a reason
        # to stop: the caller carries its last live state or records a gap, and goes on to the other subjects.
        empty = [None] * len(sessions)
        return {gate: (list(empty), [list(empty) for _ in registration["gates"][gate]["seeds"]]) for gate in subject["gates"]}
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
    """Every registered shadow ticket for ``session``, written to ``records`` unless ``dry_run``. One run at a time per issuer."""
    machine = machine or socket.gethostname()
    path = registration_path or root / REGISTRATION
    if dry_run:
        return _emit(root, snapshot, session, records, now, machine, "0" * 40, only, path, clock)
    # Refused before anything is touched: a run that may not write leaves no trace in the records.
    commit = refuse_unless_registered(root, json.loads(path.read_text(encoding="utf-8")), path, machine)
    with only_run(records, machine):
        return _emit(root, snapshot, session, records, now, machine, commit, only, path, clock)


def _emit(root: Path, snapshot: Path, session: date, records: Path | None, now: datetime | None, machine: str,
          commit: str, only: list[str] | None, path: Path, clock) -> Batch:
    """The work of ``emit``. ``commit`` is the registered code commit, or forty zeros for a rehearsal.

    ``clock`` gives the current time and is read again immediately before each ticket is
    written: that reading is the ticket's issuance time and decides whether it is live.
    ``now`` fixes the clock, for tests. A subject whose close for the session was not knowable
    before the decision time gets no ticket: an earlier day's values never stand in for a
    session that has not been read. A gate that has no state yet gets none either.
    """
    registration = json.loads(path.read_text(encoding="utf-8"))
    dry_run = commit == "0" * 40
    clock = clock or ((lambda: now) if now is not None else (lambda: datetime.now(timezone.utc)))
    if dry_run and pinned_differences(root, registration):
        print(f"note: these files differ from the ones the registration pins: {', '.join(pinned_differences(root, registration))}")
    P, G, spec = registration["parameters"], registration["parameters"]["gate"], registration["inputs"]
    subjects = [s for s in registration["subjects"] if only is None or s["subject"] in only]
    if not dry_run:  # a gate's record starts on its registered first session: nothing is written for an earlier one
        started = lambda s, gate: first_session_of(s, gate) is not None and str(session) >= first_session_of(s, gate)
        for s in subjects:
            early = [gate for gate in s["gates"] if not started(s, gate)]
            if early:
                print(f"{s['subject']} {', '.join(early)}: {session} is before the registered first session: no ticket")
        subjects = [{**s, "gates": [gate for gate in s["gates"] if started(s, gate)]} for s in subjects]
        subjects = [s for s in subjects if s["gates"]]
    zone = ZoneInfo("America/New_York")
    cutoff = datetime.combine(session, time.fromisoformat(spec["decision_time"]), zone)
    if clock() < cutoff:
        raise SystemExit(f"it is before the decision time {cutoff.isoformat()}: no ticket is written early")
    inputs = build(spec, root, snapshot, {s["subject"]: (s["open"], s["close"]) for s in subjects}, session)
    # The registration pins files at their paths; the builder read the ones the snapshot's manifest points to. They must be the same.
    registered = {Path(name).name: value for name, value in registration.get("pinned_files", {}).items()}
    used = next(iter(inputs.hashes.values()), {})
    strangers = sorted(name for name, value in used.items() if name in registered and registered[name] != value)
    if strangers and not dry_run:
        raise SystemExit(f"the snapshot was built with files that differ from the ones the registration pins: {', '.join(strangers)}")
    if strangers:
        print(f"note: the snapshot uses files that differ from the ones the registration pins: {', '.join(strangers)}")
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
            try:  # one gate that fails does not stop the others
                common["gate"] = gate
                earlier = None if records is None else last_live_ticket(records, machine, gate, folder, session)
                place = (records, machine, gate, folder, session)
                missing = tuple(n for n, v in row.items() if v is None)
                why = None if mean[-1] is not None else "missing_input" if missing else "no_fit"
                if mean[-1] is None:  # no score: the state is not recomputed, it is the last one this issuer actually published in time
                    if earlier is None:
                        cause = "an input was not knowable at the cutoff" if missing else "the month's model could not be fitted although every input was there"
                        gap = ShadowGap(gap_id=f"gap:{gate}:{subject_id}:{session}", issued_at=clock(), missing_inputs=missing,
                                        reason=f"no score: {cause}; and no earlier live ticket of this gate and subject to hold", **common)
                        official = gap if dry_run else publish(*place, gap, batch)
                        (batch.gaps if isinstance(official, ShadowGap) else batch.tickets).append(official)
                        continue
                    state, level, run, continues, previous_hash = earlier[0].state, None, (), "held", earlier[1]
                else:
                    before = [v for v in mean[:-1] if v is not None]
                    level = score_level(before, mean[-1], G["red_quantile"], G["yellow_quantile"], G["window"], G["min_scores"])
                    if level is None:
                        print(f"{name} {gate}: the gate has no state yet: no ticket")
                        continue
                    if earlier is None:  # the gate's first ticket: the registered procedure on this snapshot's history
                        position, below = gate_position(mean, G["red_quantile"], G["yellow_quantile"], G["window"], G["min_scores"], G["step_down_after"])
                        continues, previous_hash = "first", None
                    else:
                        last, previous_hash = earlier
                        day_before = inputs.sessions[-2] if len(inputs.sessions) > 1 else None
                        follows = last.session == day_before and not last.held
                        # After a held, missed or late session the issued state stands and the count starts again: nothing a later
                        # download fills in can make those sessions count.
                        position, below = step(STATES.index(last.state), list(last.run) if follows else [], level, G["step_down_after"])
                        continues = "previous_session" if follows else "after_gap"
                    state, run = STATES[position], tuple(below)
                issued = clock()  # read now, with the work done: this is when the ticket exists
                ticket = ShadowTicket(ticket_id=f"shadow:{gate}:{subject_id}:{session}", issued_at=issued,
                                      issuance="live" if next_open is not None and issued < next_open else "replay",
                                      state=state, target_exposure=EXPOSURE[state], held=mean[-1] is None, no_score_reason=why, continues=continues,
                                      previous_ticket_sha256=previous_hash, level=level, run=run, score=mean[-1],
                                      seed_scores=() if mean[-1] is None else tuple(s[-1] for s in by_seed), model_month=f"{session:%Y-%m}",
                                      inputs=row, horizon_sessions=P["horizon_sessions"], **common)
                official = ticket if dry_run else publish(*place, ticket, batch)
                (batch.tickets if isinstance(official, ShadowTicket) else batch.gaps).append(official)
            except Exception as error:
                batch.errors.append(f"{name} {gate}: {type(error).__name__}: {error}")
    batch.digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
    if batch.conflicts or batch.errors:
        raise SystemExit("\n".join(
            (["the batch was written except for records that differ from ones already there, which were left alone:"] + [f"  {c}" for c in batch.conflicts] if batch.conflicts else [])
            + (["these gates could not be processed; the others were:"] + [f"  {e}" for e in batch.errors] if batch.errors else [])))
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
        print(f"{t.ticket_id}: {t.state}, exposure {t.target_exposure}, score {None if t.score is None else round(t.score, 4)}, held {t.held}, {t.issuance}, "
              f"{t.continues}" + (" (already written, kept)" if t.ticket_id in batch.kept else ""))
    for g in batch.gaps:
        print(f"{g.gap_id}: no ticket; missing {list(g.missing_inputs)}" + (" (already written, kept)" if g.gap_id in batch.kept else ""))
    for t in batch.replays:
        print(f"{t.ticket_id}: a gap is the official record of this decision; what is computable now was written as a replay ({t.state})")
    new_tickets = sum(t.ticket_id not in batch.kept for t in batch.tickets)
    new_gaps = sum(g.gap_id not in batch.kept for g in batch.gaps)
    print(("dry run, nothing written" if args.dry_run else
           f"{new_tickets} tickets and {new_gaps} gaps written, {len(batch.kept)} records kept, "
           f"{len(batch.replays) - len(batch.reused)} replays written and {len(batch.reused)} reused") + f"; scores digest {batch.digest}")


if __name__ == "__main__":
    main()
