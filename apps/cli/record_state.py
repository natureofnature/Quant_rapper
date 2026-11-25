"""How one machine's folder in the records repository differs from the last commit. Shared by the daily job and the morning check."""
from pathlib import Path
import subprocess

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
