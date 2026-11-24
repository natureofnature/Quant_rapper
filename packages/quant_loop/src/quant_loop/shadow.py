"""Shadow tickets: what each registered fitted gate says after a session, written beside the rule's ticket.

    python -m quant_loop.shadow emit --session 2026-11-24 --snapshot artifacts/snapshots/2026-11-24 --records ../Quant_rapper_runtime_data
    python -m quant_loop.shadow emit ... --dry-run     compute and print, write nothing

A shadow ticket decides nothing. Everything comes from the registration: subjects, gates,
inputs, model and thresholds. The inputs are built from one snapshot through the bindings
catalogue, each as knowable before the decision time on its session. The model is refitted
on the whole history at every run, as the walk-forward does, so the score of the session
is the one the registered procedure gives for that snapshot.

Writing is refused while the registration is a draft, and unless the code is the commit the
registration pins. A ticket goes to <records>/<machine>/tickets/<gate>/<subject>/<session>.json,
once. ``--dry-run`` skips both checks and writes nothing, for rehearsal and for comparing two machines:
it prints a digest of every score, which must be equal on both for the same snapshot.
"""

import argparse
from datetime import date, datetime, time, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
from zoneinfo import ZoneInfo

import numpy as np

from quant_contracts.forecast import EXPOSURE, ShadowTicket
from quant_contracts.pit import SeriesBinding
from quant_evaluator.beta.forward_target import class_thresholds, forward_average_return, trailing_volatility
from quant_evaluator.beta.model_gate import gate_states, walk_forward_binary
from quant_evaluator.beta.risk_gate import forward_drawdown
from quant_loop.tickets import write_once
from quant_loop.wide_inputs import BINDINGS, build

REGISTRATION = "artifacts/forward/shadow_gates/registration.json"
STATES = ("GREEN", "YELLOW", "RED")
PINNED_PATHS = ("packages", "apps/cli")  # the code a registered ticket may be written by


def refuse_unless_registered(root: Path, registration: dict, path: Path) -> str:
    """The registered code commit, after checking that this checkout is that code and this registration is the committed one."""
    if str(registration.get("status", "")).startswith("DRAFT"):
        raise SystemExit("the registration is a draft: no ticket is written under it (use --dry-run to rehearse)")
    commit = (registration.get("code") or {}).get("commit") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit("the registration pins no code commit")
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True)
    if git("diff", "--quiet", "HEAD", "--", str(path.relative_to(root))).returncode or not git("ls-files", "--", str(path.relative_to(root))).stdout.strip():
        raise SystemExit("the registration must be the committed file, unchanged")
    if git("diff", "--quiet", commit, "--", *PINNED_PATHS).returncode:
        raise SystemExit(f"the code differs from the registered commit {commit[:7]}: no ticket is written by other code")
    return commit


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
         dry_run: bool = False, only: list[str] | None = None, registration_path: Path | None = None) -> tuple[list[ShadowTicket], str]:
    """Every registered shadow ticket for ``session`` and a digest of all scores. Written to ``records`` unless ``dry_run``.

    A subject whose close for the session was not knowable before the decision time gets no
    ticket: an earlier day's values never stand in for a session that has not been read. A gate
    that has no state yet gets none either.
    """
    path = registration_path or root / REGISTRATION
    registration = json.loads(path.read_text(encoding="utf-8"))
    commit = "0" * 40 if dry_run else refuse_unless_registered(root, registration, path)
    now, machine = now or datetime.now(timezone.utc), machine or socket.gethostname()
    P, G, spec = registration["parameters"], registration["parameters"]["gate"], registration["inputs"]
    subjects = [s for s in registration["subjects"] if only is None or s["subject"] in only]
    inputs = build(spec, root, snapshot, {s["subject"]: (s["open"], s["close"]) for s in subjects}, session)
    cutoff = datetime.combine(session, time.fromisoformat(spec["decision_time"]), ZoneInfo(inputs.equity.timezone))
    if now < cutoff:
        raise SystemExit(f"it is {now.isoformat()}, before the decision time {cutoff.isoformat()}: no ticket is written early")
    catalogue = {s["series_id"]: SeriesBinding(**s) for s in json.loads((root / BINDINGS / "series_bindings.json").read_text(encoding="utf-8"))["series"]}
    later = [s for s in inputs.equity.sessions if s > session]
    tickets, record = [], {}
    for subject in subjects:
        name = subject["subject"]
        closes = inputs.prices[name][2]
        if session not in closes or closes[session].available_at >= cutoff:
            print(f"{name}: its close for {session} was not knowable before {cutoff.time()}: no ticket")
            continue
        next_open = datetime.combine(later[0], catalogue[subject["open"]].value_time, cutoff.tzinfo) if later else None
        issuance = "live" if next_open is not None and now < next_open else "replay"
        subject_id = catalogue[subject["close"]].anchors[0]
        row = {n: (None if not np.isfinite(v) else float(v)) for n, v in zip(inputs.names, inputs.matrices[name][-1])}
        for gate, (mean, by_seed) in scores_of(registration, inputs, subject).items():
            record[f"{name}:{gate}"] = [[None if v is None else repr(v) for v in seed] for seed in by_seed]
            state, held = gate_states(mean, G["red_quantile"], G["yellow_quantile"], G["window"], G["min_scores"], G["step_down_after"])[-1]
            if state is None:
                print(f"{name} {gate}: the gate has no state yet: no ticket")
                continue
            tickets.append(ShadowTicket(
                ticket_id=f"shadow:{gate}:{subject_id}:{session}", gate=gate, subject_id=subject_id, session=session, decision_time=cutoff,
                issued_at=now, issuance=issuance, machine=machine, state=STATES[state], target_exposure=EXPOSURE[STATES[state]], held=held,
                score=mean[-1], seed_scores=() if mean[-1] is None else tuple(s[-1] for s in by_seed), model_month=f"{session:%Y-%m}",
                inputs=row, horizon_sessions=P["horizon_sessions"], registration_id=registration["registration_id"],
                registration_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), code_commit=commit, inputs_sha256=inputs.hashes))
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
    if not dry_run:
        for ticket in tickets:
            subject = ticket.subject_id.replace(":", "_")
            write_once(records / machine / "tickets" / ticket.gate / subject / f"{session}.json", ticket.model_dump_json(indent=1) + "\n")
    return tickets, digest


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
    tickets, digest = emit(args.root, args.snapshot.resolve(), args.session, args.records, dry_run=args.dry_run, only=args.subject)
    for t in tickets:
        print(f"{t.ticket_id}: {t.state}, exposure {t.target_exposure}, score {None if t.score is None else round(t.score, 4)}, held {t.held}, {t.issuance}")
    print(f"{'dry run, nothing written' if args.dry_run else f'{len(tickets)} tickets written'}; scores digest {digest}")


if __name__ == "__main__":
    main()
