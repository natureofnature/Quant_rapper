"""Build a registered input set for each subject from one snapshot, through the bindings catalogue.

The shared form of what RFC-005's script did for itself. Every series is read by its
catalogue binding, so first-publication floors, repaired closes and unfinished bars are
handled by the loaders; each file is found through the snapshot's manifest; and the count of
sessions to the next FOMC meeting uses the schedule as it stood on each date. Nothing here
reads an outcome.
"""

from datetime import date, time
import hashlib
import json
from math import log, sqrt
from pathlib import Path

import numpy as np

from quant_contracts.causal_map import SUBJECT_CLOSE, ResearchInputs
from quant_contracts.evaluation import Factor
from quant_contracts.pit import Observation, SeriesBinding, SessionCalendar
from quant_loop.feature_panel import feature_panel
from quant_pit_data.asof import load_daily_closes, on_calendar, ratio_series
from quant_pit_data.connectors.yahoo_chart import close_dates, load_closes, load_daily
from quant_pit_data.schedule import scheduled_as_known, sessions_to_next
from quant_pit_data.snapshots import problems

BINDINGS = "knowledge_base/prior_knowledge/bindings"
DERIVED = "derived from "


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Inputs:
    """The matrices and prices of one build. ``hashes`` gives, per subject, the hash of every file that subject's inputs came from.

    A subject's list holds the shared files and its own price file, never another subject's:
    its ticket is then the same whichever other subjects were built in the same run.
    """

    def __init__(self, sessions, names, matrices, prices, hashes, equity):
        self.sessions, self.names, self.matrices, self.prices, self.hashes, self.equity = sessions, names, matrices, prices, hashes, equity


def build(spec: dict, root: Path, snapshot: Path, subjects: dict[str, tuple[str, str]], last: date) -> Inputs:
    """One row per session up to ``last`` and one column per input of ``spec``, for each subject.

    ``spec`` is a registration's input block: ``first_session``, ``decision_time``,
    ``base_factors`` (shared, per_subject, from_factor_definitions), ``input_definitions``,
    ``series_on_their_own_dates``, and ``fomc_scheduled_after_file``. ``subjects`` maps a
    name to its (open, close) series ids. A value that was not knowable before the decision
    time on its session is missing, and missing stays missing.
    """
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    catalogue = json.loads((root / BINDINGS / "series_bindings.json").read_text(encoding="utf-8"))
    b = {s["series_id"]: SeriesBinding(**s) for s in catalogue["series"]}
    research = ResearchInputs.model_validate_json((root / BINDINGS / "factor_definitions.json").read_text(encoding="utf-8"))
    definitions = {d.factor.factor_id: d.factor for d in research.factors}
    inputs = list(spec["input_definitions"])
    needed = {a for name in inputs for a in spec["input_definitions"][name]["of"]}
    base = spec["base_factors"]
    shared = [Factor(**f) for f in base["shared"] if f["factor_id"] in needed]
    per_subject = [dict(f) for f in base["per_subject"] if f["factor_id"] in needed]
    own = [definitions[n] for n in base["from_factor_definitions"]]
    read = {i for f in (*shared, *own) for i in f.series_ids} | {i for f in per_subject for i in f["series_ids"]}
    read -= {SUBJECT_CLOSE}
    parts = {i: tuple(b[i].source_url[len(DERIVED):].split(" and ")) for i in read if b[i].source_url.startswith(DERIVED)}
    downloaded = (read - set(parts)) | {p for pair in parts.values() for p in pair} | {i for pair in subjects.values() for i in pair}
    file_of = {series_id: name for name, entry in manifest["files"].items() for series_id in entry["series_ids"]}
    missing = sorted(downloaded - set(file_of))
    if missing:
        raise ValueError(f"the snapshot's manifest lists no file for {missing}")
    names = sorted({file_of[i] for i in downloaded})
    wrong = problems(snapshot, names)  # only what this build reads, by the rule the loaders apply
    if wrong:
        raise ValueError(f"the snapshot is not sound for these inputs: {'; '.join(wrong)}")
    def with_records(files: set[str]) -> dict[str, str]:
        found = {name: manifest["files"][name]["sha256"] for name in files}
        found.update({manifest["files"][n]["record_file"]["name"]: manifest["files"][n]["record_file"]["sha256"] for n in files if "record_file" in manifest["files"][n]})
        return found

    in_common = {file_of[i] for i in (read - set(parts)) | {p for pair in parts.values() for p in pair}}  # what every subject's inputs read
    hashes = with_records(in_common)

    def pinned(name: str) -> Path:
        entry = manifest["shared"][name]
        if digest(root / entry["path"]) != entry["sha256"]:
            raise ValueError(f"{name} does not match the snapshot manifest")
        hashes[name] = entry["sha256"]
        return root / entry["path"]

    calendar = lambda name: SessionCalendar(**json.loads(pinned(name).read_text(encoding="utf-8")))
    equity, treasury = calendar("us_equity_sessions_2002_2026.json"), calendar("us_treasury_yield_sessions_2003_2026.json")
    for tracked in ("series_bindings.json", "factor_definitions.json", "event_schedules.json"):
        hashes[tracked] = digest(root / BINDINGS / tracked)
    sessions = [s for s in equity.sessions if date.fromisoformat(spec["first_session"]) <= s <= last]
    if not sessions or sessions[-1] != last:
        raise ValueError(f"{last} is not a session of {equity.calendar_id}")

    # A subject's prices are read once, as open and close; its close can also be an input of every subject.
    quotes = {subject: load_daily(snapshot / file_of[close_id], b[open_id], b[close_id]) for subject, (open_id, close_id) in subjects.items()}
    own_dates, calendars = set(spec["series_on_their_own_dates"]), {"treasury:real_yield_10y": treasury}
    rows_of = {close_id: [q for q in quotes[subject] if q.series_id == close_id] for subject, (_, close_id) in subjects.items()}
    for series_id in sorted(downloaded - {i for pair in subjects.values() for i in pair}):
        path = snapshot / file_of[series_id]
        rows = load_daily_closes(path, b[series_id]) if path.suffix == ".csv" else load_closes(path, b[series_id])
        if series_id in own_dates:  # read on the dates the source has a value for; a floored value leaves its date a session with no value
            days = tuple(close_dates(path))
            calendars[series_id] = SessionCalendar(calendar_id=f"dates_of:{series_id}", timezone=b[series_id].timezone, first=days[0], last=days[-1],
                                                   sessions=days, basis_note="The dates on which the snapshot's file has a value.")
        elif series_id not in calendars:
            rows, _ = on_calendar(rows, equity)
        rows_of[series_id] = rows
    for series_id, (top, bottom) in parts.items():
        rows_of[series_id] = ratio_series(b[series_id], rows_of[top], rows_of[bottom])
    observations: list[Observation] = [o for i in read for o in rows_of[i]]

    held = [date.fromisoformat(d) for d in json.loads(pinned("announcement_dates_2002_2026.json").read_text(encoding="utf-8"))["fomc"]["scheduled"]
            + spec["fomc_scheduled_after_file"]]
    schedules = json.loads((root / BINDINGS / "event_schedules.json").read_text(encoding="utf-8"))
    withdrawn = [(date.fromisoformat(w["scheduled"]), date.fromisoformat(w["known_on"])) for w in schedules["fomc"]["withdrawn"]]

    def compose(definition: dict, row: dict, session: date) -> float | None:
        if definition["op"] == "calendar":  # None once the calendar ends before the next meeting: the gate then holds
            count = sessions_to_next(equity, session, scheduled_as_known(held, withdrawn, session))
            return None if count is None else float(count)
        values = [row.get(a) for a in definition["of"]]
        if any(v is None for v in values):
            return None
        a, c = values[0], (values[1] if len(values) > 1 else None)
        try:
            return {"base": lambda: a, "log": lambda: log(a), "log1p": lambda: log(1.0 + a), "minus": lambda: a - c,
                    "relative": lambda: (1.0 + a) / (1.0 + c) - 1.0, "over": lambda: a / c,
                    "implied_minus_realised": lambda: a / 100.0 - c * sqrt(252.0)}[definition["op"]]()
        except (ValueError, ZeroDivisionError):
            return None  # undefined for these values: missing, never a number

    decision = time.fromisoformat(spec["decision_time"])
    shared_panel = feature_panel("shared", sessions, decision, shared, b, observations, equity, calendars)
    matrices, prices = {}, {}
    for subject, (open_id, close_id) in subjects.items():
        mine = lambda ids: tuple(close_id if i == SUBJECT_CLOSE else i for i in ids)
        factors = [f.model_copy(update={"series_ids": mine(f.series_ids)}) for f in own] + [Factor(**{**f, "series_ids": mine(f["series_ids"])}) for f in per_subject]
        extra = [] if close_id in read else rows_of[close_id]  # already among the observations when a shared input reads it
        panel = feature_panel(subject, sessions, decision, factors, b, observations + extra, equity, calendars)
        rows = [{**s.features, **p.features} for s, p in zip(shared_panel, panel)]
        matrices[subject] = np.array([[np.nan if (v := compose(spec["input_definitions"][name], row, session)) is None else v for name in inputs]
                                      for row, session in zip(rows, sessions)], dtype=float)
        opens = {q.session: q.value for q in quotes[subject] if q.series_id == open_id}
        closes = {q.session: q for q in rows_of[close_id]}
        prices[subject] = ([opens.get(s) for s in sessions], [closes[s].value if s in closes else None for s in sessions], closes)
    by_subject = {subject: dict(sorted({**hashes, **with_records({file_of[open_id], file_of[close_id]})}.items())) for subject, (open_id, close_id) in subjects.items()}
    return Inputs(sessions, inputs, matrices, prices, by_subject, equity)
