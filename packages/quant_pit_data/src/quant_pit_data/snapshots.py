"""What a written snapshot folder must still hold for its files to be read as its manifest describes them.

A downloaded file can have a record beside it, ``<name>.record.json``, written by the snapshot
command: which closes were filled in at download and when the quote that filled them was
stamped, and whether the file's last bar is a quote from a trading period that had not ended.
The loaders read that record, and a file read without it would be dated and counted wrongly.
So the manifest pins each record by hash, and a snapshot is used only after this check.
No third-party imports: the snapshot command runs without them.
"""

import hashlib
import json
from pathlib import Path


def record_name(name: str) -> str:
    return f"{Path(name).stem}.record.json"


def problems(folder: Path) -> list[str]:
    """Every file or record of a snapshot that is missing or no longer matches the manifest, as readable lines. Empty when sound."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    found = []
    for name, entry in manifest["files"].items():
        checks = [(name, folder / name, entry["sha256"])]
        if "record_file" in entry:
            checks.append((f"the record of {name}", folder / entry["record_file"]["name"], entry["record_file"]["sha256"]))
        for label, path, expected in checks:
            if not path.is_file():
                found.append(f"{label} is missing")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                found.append(f"{label} does not match the manifest")
    return found
