"""The records of the forward test on one machine, and the one question every reader of them must answer the same way:
which record is the authoritative one for a gate, a subject and a session?

A decision has one official record, a ticket or a gap. What is committed decides. If one of
the two is in the last commit, it is the record, whatever else is or is not on disk: a file
placed beside it later is a stray, and deleting it locally does not make it stop being the
record. If neither is committed, the record is the one that is on disk, and if both are there
nothing can be said. Issuance, the hold of an earlier state, publication and the morning check
all go through ``Records.official``, so they cannot disagree.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess

from quant_contracts.forecast import BetaGap, BetaTicket, ShadowGap, ShadowTicket

LOCKS = ("job.lock", ".lock")  # the two files a run holds while it works; nothing else is exempt


def record_state(records: Path, machine: str) -> tuple[list[str], list[str], list[str]]:
    """What differs from the last commit under this machine's folder, as three lists of paths.

    new: files in no commit, on disk exactly as they would be committed. They are what a run publishes.
    changed: committed records that were edited, deleted or moved on disk, and the places they were moved to.
    conflicts: files an earlier run staged whose copy on disk has since changed, so two versions of one record exist.

    What decides is whether a path is in the last commit, not how git labels it: a file staged
    by a run whose commit then failed is still new. A move is a change to the record that was
    moved, wherever it was moved to, and what it was moved to is not a new record.
    """
    git = lambda *a: subprocess.run(["git", "-C", str(records), *a], capture_output=True, text=True)
    status = git("status", "--porcelain", "--untracked-files=all", "-z", "--", machine)
    if status.returncode:
        raise OSError(f"reading the state of the records: {status.stderr.strip() or status.returncode}")
    committed = set(git("ls-tree", "-r", "--name-only", "-z", "HEAD", "--", machine).stdout.split("\0"))
    locks = {f"{machine}/{name}" for name in LOCKS}
    new, changed, conflicts, tokens = [], [], [], iter(status.stdout.split("\0"))
    for entry in tokens:
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        moved_from = next(tokens, None) if code[0] in "RC" or code[1] in "RC" else None  # a rename or copy is followed by the path it came from
        if moved_from is not None:  # looked at first: a record moved onto a lock's name is still a record that was moved
            changed += [moved_from, path]
        elif path in locks:
            continue
        elif path in committed:
            changed.append(path)
        elif code[0] == "A" and code[1] != " ":  # staged by an earlier run, and the file has changed since: which one is the record?
            conflicts.append(path)
        else:
            new.append(path)
    return new, changed, conflicts


def committed_paths(records: Path, machine: str) -> set[str]:
    """Every path under this machine's folder that is in the last commit."""
    listed = subprocess.run(["git", "-C", str(records), "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", machine], capture_output=True, text=True)
    if listed.returncode:
        raise OSError(f"reading the committed records: {listed.stderr.strip() or listed.returncode}")
    return {name for name in listed.stdout.split("\0") if name}


def read_record(path: Path, base: Path):
    """The record at ``path`` if it is a complete record of the decision its place names; otherwise the reason it is not, as text.

    A record's place is <kind>/<gate>/<subject>/<session>.json under the machine's folder
    ``base``. The file must pass the same schema issuance uses, and its own gate, subject and
    session must be the ones of its place: a copy of another session's or another subject's
    ticket is not this decision's ticket, and neither is a file with a field missing.
    """
    kind, gate, subject, name = path.relative_to(base).parts
    model = {"tickets": BetaTicket if gate == "rule" else ShadowTicket, "gaps": BetaGap if gate == "rule" else ShadowGap, "replays": ShadowTicket}[kind]
    try:
        record = model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:  # pydantic's validation error is a ValueError
        return f"it is not a complete {model.__name__}: {str(error).splitlines()[0]}"
    holds = (getattr(record, "gate", "rule"), record.subject_id.replace(":", "_"), str(record.session))
    if holds != (gate, subject, Path(name).stem):
        return f"it holds another decision, {' '.join(holds)}"
    writer = getattr(record, "machine", None)  # a shadow record names its issuer; the rule's does not
    if writer is not None and writer != base.name:
        return f"it was written by another machine, {writer}"
    return record


def other_record(path: str) -> str | None:
    """For a ticket's path the gap's of the same decision, and the reverse; None for anything else. One decision has one official record."""
    parts = Path(path).parts
    if len(parts) < 4 or parts[-4] not in ("tickets", "gaps"):
        return None
    return Path(*parts[:-4], "gaps" if parts[-4] == "tickets" else "tickets", *parts[-3:]).as_posix()


def misplaced(path: Path, base: Path) -> str | None:
    """Why the file at ``path`` is not the record its place names, or None when it is, or when it is not a decision's record at all."""
    parts = path.relative_to(base).parts
    if len(parts) != 4 or parts[0] not in ("tickets", "gaps", "replays"):
        return None  # a manifest, a run log
    found = read_record(path, base)
    return found if isinstance(found, str) else None


@dataclass(frozen=True)
class Official:
    """The authoritative record of one decision. ``kind`` is "ticket", "gap", or None when there is none or it cannot be said."""

    kind: str | None
    path: Path | None
    stray: Path | None = None  # the other kind's file, present on disk beside a committed record: never the record
    problem: str | None = None  # why no record can be named, or why the named one cannot be used


class Records:
    """One machine's folder in the records repository, read against its last commit."""

    def __init__(self, records: Path, machine: str, committed: set[str] | None = None):
        self.records, self.machine, self.base = records, machine, records / machine
        if committed is None:  # a folder that is not a git checkout has nothing committed: what is on disk is all there is
            committed = committed_paths(records, machine) if (records / ".git").exists() else set()
        self.committed = committed

    def official(self, gate: str, subject: str, session) -> Official:
        ticket, gap = (self.base / kind / gate / subject / f"{session}.json" for kind in ("tickets", "gaps"))
        kind_of = {ticket: "ticket", gap: "gap"}
        on_record = [p for p in (ticket, gap) if p.relative_to(self.records).as_posix() in self.committed]
        if len(on_record) == 2:
            return Official(None, None, problem="a ticket and a gap are both committed for this decision")
        if on_record:
            chosen = on_record[0]
            other = gap if chosen is ticket else ticket
            missing = None if chosen.is_file() else f"the committed {kind_of[chosen]} of this decision is missing from disk"
            return Official(kind_of[chosen], chosen, stray=other if other.is_file() else None, problem=missing)
        present = [p for p in (ticket, gap) if p.is_file()]
        if len(present) == 2:
            return Official(None, None, problem="a ticket and a gap both exist for this decision and neither is committed")
        return Official(kind_of[present[0]], present[0]) if present else Official(None, None)

    def last_live(self, gate: str, subject: str, session):
        """(the latest ticket before ``session`` that was in force for this gate and subject, its file's hash), or None.

        A ticket was in force only if it is the official record of its decision, is complete, is
        the decision its place names, and was written in time. A session whose record is a gap,
        or cannot be named, is passed over: the state before it still stands.
        """
        folder = self.base / "tickets" / gate / subject
        prefix = folder.relative_to(self.records).as_posix() + "/"
        days = {p.stem for p in folder.glob("*.json")} | {Path(name).stem for name in self.committed if name.startswith(prefix)}
        for day in sorted((d for d in days if d < str(session)), reverse=True):
            found = self.official(gate, subject, day)
            if found.kind != "ticket" or found.problem:
                continue
            record = read_record(found.path, self.base)
            if not isinstance(record, str) and record.issuance == "live":
                return record, hashlib.sha256(found.path.read_bytes()).hexdigest()
        return None
