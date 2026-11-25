"""The daily job: snapshot, the rule's tickets, the shadow tickets, then the records repository.

    python apps/cli/daily_job.py --records ../Quant_rapper_runtime_data
    python apps/cli/daily_job.py --records ../Quant_rapper_runtime_data --session 2026-11-24 --no-push

For the latest US session that has closed, or a named one:
  1. take the snapshot, or check the one already taken;
  2. write the frozen rule's ticket for each subject of its registration;
  3. write the shadow tickets of the fitted gates, if their registration is registered;
  4. copy the snapshot's manifest into the records, write a log of this run, commit and push.

Everything goes under <records>/<machine>/, the machine being this computer's name. Each stage
keeps its own guards; this script only orders them. A stage that fails is logged and the run
exits non-zero, so a scheduler retries it; whatever an earlier stage wrote stays. Running it
again for the same session writes nothing twice: records already there are kept.

A draft registration is not an error: the shadow stage is skipped and says so.
"""
import argparse
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time as clock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/cli"))
for package in ("quant_contracts", "quant_pit_data", "quant_evaluator", "quant_causal_graph", "quant_loop"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
import snapshot as snapshots
from quant_contracts.forecast import BetaGap, BetaTicket
from quant_contracts.pit import SeriesBinding
from quant_loop import shadow, tickets
from quant_pit_data.snapshots import problems

RULE = "artifacts/ablations/sector_risk_baseline/registration.json"
BINDINGS = "knowledge_base/prior_knowledge/bindings/series_bindings.json"


def said(error: BaseException) -> str:
    return str(error) or type(error).__name__


def rule_tickets(root: Path, folder: Path, session: date, base: Path, now: datetime | None) -> dict:
    """The frozen rule's ticket for each subject of its registration, written once under ``base``/tickets/rule/."""
    registration = json.loads((root / RULE).read_text(encoding="utf-8"))
    catalogue = {s["series_id"]: SeriesBinding(**s) for s in json.loads((root / BINDINGS).read_text(encoding="utf-8"))["series"]}
    out = {"written": [], "gaps": [], "kept": [], "failed": {}}
    for subject in registration["subjects"]:
        symbol = subject["subject"]
        name = catalogue[subject["close"]].anchors[0].replace(":", "_")
        where, gap_path = base / "tickets" / "rule" / name, base / "gaps" / "rule" / name / f"{session}.json"
        path = where / f"{session}.json"
        try:
            if path.is_file():  # written by an earlier run: it stands, with the time it was written
                out["kept"].append(BetaTicket.model_validate_json(path.read_text(encoding="utf-8")).ticket_id)
                continue
            if gap_path.is_file():  # one decision has one official record, and the first one stands
                out["kept"].append(BetaGap.model_validate_json(gap_path.read_text(encoding="utf-8")).gap_id)
                continue
            ticket = tickets.emit(root, folder, symbol, session, now=now, folder=where)  # with no fixed time it reads the clock as it writes
            tickets.write_once(path, ticket.model_dump_json(indent=1) + "\n")
            out["written"].append(f"{ticket.ticket_id}: {ticket.state}, held {ticket.held}, {ticket.issuance}")
        except tickets.NothingToHold as nothing:  # no value and no live ticket to hold: on record as a gap, never as a state
            tickets.write_once(gap_path, nothing.gap.model_dump_json(indent=1) + "\n")
            out["gaps"].append(nothing.gap.gap_id)
        except (SystemExit, Exception) as error:  # one subject's failure does not stop the others
            out["failed"][symbol] = said(error)
    return out


def shadow_tickets(root: Path, folder: Path, session: date, records: Path, machine: str, clock_now) -> dict:
    registration = json.loads((root / shadow.REGISTRATION).read_text(encoding="utf-8"))
    if str(registration.get("status", "")).startswith("DRAFT"):
        return {"skipped": "the shadow registration is a draft: no shadow ticket is written"}
    try:
        batch = shadow.emit(root, folder, session, records, machine=machine, clock=clock_now)
    except (SystemExit, Exception) as error:
        return {"failed": said(error)}
    return {"written": [t.ticket_id for t in batch.tickets if t.ticket_id not in batch.kept], "gaps": [g.gap_id for g in batch.gaps if g.gap_id not in batch.kept],
            "kept": batch.kept, "replays": [t.ticket_id for t in batch.replays], "scores_digest": batch.digest}


def publish_records(records: Path, machine: str, session: date, push: bool, tries: int, pause: float) -> dict:
    """Commit the files this machine added, and only those, then push. A later run pushes a commit whose push failed.

    Records are written once, so publishing only ever adds files. A record already committed
    that was changed or deleted on disk is never staged: it is reported, and the remote keeps
    what it has. Every step's result is checked: a run is reported as pushed only when the new
    files are committed and the remote has that commit.
    """
    git = lambda *a: subprocess.run(["git", "-C", str(records), *a], capture_output=True, text=True)
    out = {"committed": False, "pushed": None}
    status = git("status", "--porcelain", "--untracked-files=all", "-z", "--", machine)
    if status.returncode:
        return {**out, "failed": f"reading the state of the records: {status.stderr.strip() or status.returncode}"}
    entries = [e for e in status.stdout.split("\0") if e and not e.endswith(".lock")]  # a lock is this run's own, never a record
    new, changed = [e[3:] for e in entries if e.startswith("??")], [e[3:] for e in entries if not e.startswith("??")]
    problems_found = [f"{len(changed)} committed records were changed or deleted on disk and were not published, the first being {changed[0]}"] if changed else []
    if new:
        staged = git("add", "--", *new)
        if staged.returncode:
            return {**out, "failed": f"staging the records: {staged.stderr.strip() or staged.returncode}"}
        made = git("commit", "-q", "-m", f"{machine} {session}: records of the daily job", "--", *new)  # these paths only
        if made.returncode:
            return {**out, "failed": f"commit: {made.stderr.strip() or made.returncode}"}
        out["committed"] = True
    if push:
        for attempt in range(tries):
            sent = git("push", "-q")
            if sent.returncode == 0:
                break
            clock.sleep(pause)
        else:
            return {**out, "pushed": False, "failed": "; ".join(problems_found + [f"push: {sent.stderr.strip() or sent.returncode}"])}
        here, there = git("rev-parse", "HEAD").stdout.strip(), git("rev-parse", "@{upstream}").stdout.strip()
        if not here or here != there:
            return {**out, "pushed": False, "failed": "; ".join(problems_found + ["after the push the remote does not have this commit"])}
        out["pushed"] = True
    return {**out, "failed": problems_found[0]} if problems_found else out


def run(root: Path, records: Path, session: date | None, push: bool, fetcher, now, machine: str, tries: int = 3, pause: float = 20.0, fetch_pause: float = 0.6) -> int:
    clock_now = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    started = clock_now()
    sessions = [date.fromisoformat(s) for s in json.loads((root / snapshots.CALENDAR).read_text(encoding="utf-8"))["sessions"]]
    session = session or snapshots.latest_closed_session(sessions, started)
    base = records / machine
    base.mkdir(parents=True, exist_ok=True)
    log = {"machine": machine, "session": str(session), "started": started.isoformat(), "stages": {}}
    with (base / "job.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"another daily job is running for {machine}: this one stops", file=sys.stderr)
            return 1
        folder = root / "artifacts/snapshots" / str(session)
        if folder.is_dir():
            wrong = problems(folder)
            log["stages"]["snapshot"] = {"failed": "; ".join(wrong)} if wrong else {"kept": "already taken; it matches its manifest"}
        else:
            code = snapshots.main(["--session", str(session), "--no-backup"], root=root, fetcher=fetcher, pause=fetch_pause, now=now)
            log["stages"]["snapshot"] = {"written": str(folder.relative_to(root))} if code == 0 and folder.is_dir() else {"failed": f"the snapshot command exited {code}"}
        sound = "failed" not in log["stages"]["snapshot"]
        if sound:  # the backup is completed whenever it is missing, by the snapshot command's own routine; its failure does not hold up a ticket
            copy = root / "run/backup/snapshots" / str(session)
            try:
                if copy.exists():
                    log["stages"]["backup"] = {"kept": "already copied"}
                else:
                    snapshots.back_up(folder, copy)
                    log["stages"]["backup"] = {"written": str(copy.relative_to(root))}
            except OSError as error:
                log["stages"]["backup"] = {"failed": f"the copy could not be made: {error}"}
            manifest = (folder / "manifest.json").read_bytes()
            log["snapshot_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
            recorded = base / "manifests" / f"{session}.json"
            recorded.parent.mkdir(parents=True, exist_ok=True)
            if not recorded.is_file():
                recorded.write_bytes(manifest)
            elif recorded.read_bytes() != manifest:  # this is not the snapshot the records describe: nothing is decided from it
                log["stages"]["manifest"] = {"failed": "the manifest recorded earlier for this session differs from the snapshot's; no ticket is written"}
                sound = False
        if sound:  # nothing can be decided without the session's data, as it was recorded
            log["stages"]["rule"] = rule_tickets(root, folder, session, base, now)
            log["stages"]["shadow"] = shadow_tickets(root, folder, session, records, machine, clock_now)
        log["finished"] = clock_now().isoformat()
        entry = base / "runs" / str(session) / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"  # one file per run: a rerun adds a log, it never edits one
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(log, indent=1) + "\n", encoding="utf-8")
        outcome = publish_records(records, machine, session, push, tries, pause)
    failed = {name: stage["failed"] for name, stage in log["stages"].items() if stage.get("failed")}
    if outcome.get("failed"):
        failed["records"] = outcome["failed"]
    for name, stage in log["stages"].items():
        print(f"{name}: {json.dumps(stage)}")
    print(f"records: {json.dumps(outcome)}")
    if failed:
        print("FAILED: " + "; ".join(f"{k}: {v}" for k, v in failed.items()), file=sys.stderr)
    return 1 if failed else 0


def main(arguments: list[str] | None = None, root: Path = ROOT, fetcher=snapshots.fetch, now: datetime | None = None, machine: str | None = None, pause: float = 20.0,
         fetch_pause: float = 0.6) -> int:
    parser = argparse.ArgumentParser(description="Run the daily job for one session.")
    parser.add_argument("--records", required=True, type=Path, help="the checkout of the records repository")
    parser.add_argument("--session", type=date.fromisoformat, help="default: the latest US session that has closed")
    parser.add_argument("--no-push", action="store_true", help="commit the records locally and do not push")
    args = parser.parse_args(arguments)
    if not (args.records / ".git").exists():
        print(f"{args.records} is not a git checkout: the records repository is expected there", file=sys.stderr)
        return 2
    return run(root, args.records.resolve(), args.session, not args.no_push, fetcher, now, machine or socket.gethostname(), pause=pause, fetch_pause=fetch_pause)


if __name__ == "__main__":
    sys.exit(main())
