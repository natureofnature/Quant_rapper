"""What a written snapshot folder must still hold for its files to be read as its manifest describes them.

A downloaded file can have a record beside it, ``<name>.record.json``, written by the snapshot
command: which closes were filled in at download and when the quote that filled them was
stamped, and whether the file's last bar is a quote from a trading period that had not ended.
The loaders read that record, and a file read without it, or with another one, would be dated
and counted differently. So one rule holds for checking and for loading: a record is read only
if the folder's manifest declares it, with its hash, and any other record file is an error.
No third-party imports: the snapshot command runs without them.
"""

import hashlib
import json
from pathlib import Path


def record_name(name: str) -> str:
    return f"{Path(name).stem}.record.json"


def declared_record(path: Path) -> dict:
    """The record of the downloaded file ``path``, read only as its folder's manifest declares it. Empty when it has none.

    Raises if a record file is present that no manifest declares, or if the declared one is
    missing or does not match its hash: the file's values could then not be reproduced from
    the hashes a reader records.
    """
    record, manifest = path.with_name(record_name(path.name)), path.with_name("manifest.json")
    entry = json.loads(manifest.read_text(encoding="utf-8")).get("files", {}).get(path.name, {}) if manifest.is_file() else {}
    if "repair_file" in entry:
        raise ValueError(OLD_FORMAT.format(name=path.name))
    declared = entry.get("record_file")
    if declared is None:
        if record.is_file():
            raise ValueError(f"{record.name} is not declared by a manifest beside it: a record is read only when the manifest pins it")
        return {}
    if declared["name"] != record.name or not record.is_file():
        raise ValueError(f"the record of {path.name} is missing")
    data = record.read_bytes()
    if hashlib.sha256(data).hexdigest() != declared["sha256"]:
        raise ValueError(f"the record of {path.name} does not match the manifest")
    return json.loads(data)


OLD_FORMAT = ("{name}: this manifest was written by an earlier version of the snapshot command (repair_file, *.repair.json), which the "
              "loaders no longer read. Its repair times would be lost, so the snapshot is refused; take it again or convert its records.")


def problems(folder: Path) -> list[str]:
    """Every file or record of a snapshot that is missing, changed, undeclared or in an old format, as readable lines. Empty when sound."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    found = [OLD_FORMAT.format(name=name) for name, entry in manifest["files"].items() if "repair_file" in entry]
    declared = {entry["record_file"]["name"] for entry in manifest["files"].values() if "record_file" in entry}
    found += [f"{p.name} is not declared by the manifest" for p in sorted(folder.glob("*.record.json")) if p.name not in declared]
    found += [f"{p.name} is in an old record format the loaders do not read" for p in sorted(folder.glob("*.repair.json"))]
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
