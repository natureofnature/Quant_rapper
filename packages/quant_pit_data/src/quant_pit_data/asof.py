"""As-of selection: the latest observations that were knowable before a cutoff.

The one rule: an observation may be used only if its ``available_at`` is strictly before
the cutoff. Dates are never compared directly, so a release in one timezone cannot pick
up a close from another that happened after it.
"""

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_contracts.pit import (AsOfResult, AuditNote, Cutoff, Observation, SeriesBinding,
                                 SessionCalendar, SkippedSession, WindowedAsOf)


def fixed_at(binding: SeriesBinding, session: date) -> datetime:
    """The moment the session's value is fixed, in the series' own timezone."""
    return datetime.combine(session, binding.value_time, ZoneInfo(binding.timezone))


def observe(binding: SeriesBinding, session: date, value: float,
            available_at: datetime | None = None) -> Observation:
    """Build an observation. A recorded ``available_at`` overrides the binding's rule."""
    default = fixed_at(binding, session) + timedelta(minutes=binding.publication_delay_minutes)
    return Observation(series_id=binding.series_id, session=session, value=value,
                       available_at=available_at or default,
                       availability_basis="recorded" if available_at else binding.availability_basis,
                       source_url=binding.source_url)


def load_daily_closes(path: Path, binding: SeriesBinding) -> list[Observation]:
    """Read a DATE,...,CLOSE csv with MM/DD/YYYY dates, the layout of Cboe's index history files."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [observe(binding, datetime.strptime(row["DATE"], "%m/%d/%Y").date(), float(row["CLOSE"]))
                for row in csv.DictReader(handle)]


def as_of_vintage(observations: list[Observation], cutoff: Cutoff, series_ids: tuple[str, ...]) -> list[Observation]:
    """For each requested series and session, the one version of the value that was current at the cutoff.

    Only ``series_ids`` are read and checked: rows of any other series in the same batch are
    ignored, so a bad feed cannot block a factor that does not use it.

    A revised series can hold several rows for one session, each with the time that version
    was published in ``vintage_at``. A version is eligible only if it was both available and
    published before the cutoff; of the eligible ones the latest is kept. When none is
    eligible the earliest-published row is kept instead, so the caller can still report when
    the value appeared; it fails the caller's availability check and is never used. Two rows
    for one session with the same vintage, or both with none, are an error: the order of the
    input must never decide which value is read.
    """
    def eligible(item: Observation) -> bool:
        return item.available_at < cutoff.time and (item.vintage_at is None or item.vintage_at < cutoff.time)

    groups: dict[tuple[str, date], list[Observation]] = {}
    for item in observations:
        if item.series_id not in series_ids:
            continue
        groups.setdefault((item.series_id, item.session), []).append(item)
    kept = []
    for (series_id, session), versions in groups.items():
        stamps = [v.vintage_at for v in versions]
        if len(set(stamps)) != len(stamps):  # two rows for one version: order would decide the value
            raise ValueError(f"duplicate observation for {series_id} on {session}")
        usable = [v for v in versions if eligible(v)]
        if usable:
            kept.append(max(usable, key=lambda v: (v.vintage_at is not None, v.vintage_at or v.available_at)))
        else:  # nothing usable: keep one unusable row for the audit trail, marked by a time at or after the cutoff
            first = min(versions, key=lambda v: max(v.available_at, v.vintage_at or v.available_at))
            kept.append(first.model_copy(update={"available_at": max(first.available_at, first.vintage_at or first.available_at)}))
    return kept


def latest_common(bindings: list[SeriesBinding], observations: list[Observation], cutoff: Cutoff,
                  calendar: SessionCalendar, max_age: timedelta) -> tuple[AsOfResult, tuple[AuditNote, ...]]:
    """Pick the latest expected session for which every series was knowable before the cutoff.

    Returns (result, audit). The result is decision-facing: a skipped session lists the
    series that were unavailable as of the cutoff and nothing else. The audit notes say
    why, and may hold later knowledge such as when a late value was finally published.

    Expected sessions come from ``calendar``, so a session missing from every series is a
    reported skip, not a silent holiday. ``max_age`` bounds how old the chosen session may be;
    the search stops at the first expected session older than that.
    """
    zone = ZoneInfo(calendar.timezone)
    if not calendar.first <= cutoff.time.astimezone(zone).date() <= calendar.last:
        raise ValueError(f"calendar {calendar.calendar_id!r} does not cover the cutoff")
    by_id = {b.series_id: b for b in bindings}
    ids, expected = tuple(by_id), set(calendar.sessions)
    table: dict[tuple[date, str], Observation] = {}
    for item in as_of_vintage(observations, cutoff, ids):
        if item.series_id not in by_id or not calendar.first <= item.session <= calendar.last:
            continue
        key = (item.session, item.series_id)
        if key in table:
            raise ValueError(f"duplicate observation for {item.series_id} on {item.session}")
        if item.session not in expected:
            raise ValueError(f"{item.series_id} has a value on {item.session}, which is not an expected session")
        table[key] = item

    skipped: list[SkippedSession] = []
    audit: list[AuditNote] = []

    def result(**fields) -> tuple[AsOfResult, tuple[AuditNote, ...]]:
        return AsOfResult(cutoff=cutoff, series_ids=ids, skipped=tuple(skipped), **fields), tuple(audit)

    for session in reversed(calendar.sessions):
        if all(fixed_at(b, session) >= cutoff.time for b in bindings):
            continue  # not closed yet: not expected, so not a skip
        age = cutoff.time - max(fixed_at(b, session) for b in bindings)
        if age > max_age:  # nothing older can be used, so stop: do not list all of history as skipped
            return result(status="gap", reason=f"no usable session within the limit: {session} is {age} old, "
                                               f"over the limit of {max_age}")
        unavailable = []
        for series_id in ids:
            item = table.get((session, series_id))
            if item is None:
                audit.append(AuditNote(session=session, series_id=series_id, detail="no row in the data"))
            elif item.available_at >= cutoff.time:
                audit.append(AuditNote(session=session, series_id=series_id,
                                       detail=f"published at {item.available_at.isoformat()}"))
            else:
                continue
            unavailable.append(series_id)
        if unavailable:
            skipped.append(SkippedSession(session=session, unavailable=tuple(unavailable)))
            continue
        return result(status="fallback" if skipped else "resolved", session=session,
                      observations=tuple(table[(session, i)] for i in ids))
    return result(status="gap", reason="no expected session with every series available before the cutoff")


def at_session(bindings: list[SeriesBinding], observations: list[Observation], cutoff: Cutoff,
               calendar: SessionCalendar, session: date, max_age: timedelta) -> AsOfResult:
    """The values of one named session, if every series was knowable before the cutoff and it is within ``max_age``.

    For a value that belongs to a particular period, such as a reported figure. No other
    session stands in: if the named one cannot be used the result is a gap that says why.
    """
    ids = tuple(b.series_id for b in bindings)

    def gap(reason: str) -> AsOfResult:
        return AsOfResult(cutoff=cutoff, series_ids=ids, status="gap", reason=reason)

    if session not in set(calendar.sessions):
        return gap(f"{session} is not an expected session of {calendar.calendar_id}")
    fixed = max(fixed_at(b, session) for b in bindings)
    if fixed >= cutoff.time:
        return gap(f"{session} had not closed before the cutoff")
    if cutoff.time - fixed > max_age:
        return gap(f"{session} is {cutoff.time - fixed} old, over the limit of {max_age}")
    found = {o.series_id: o for o in as_of_vintage(observations, cutoff, ids)
             if o.session == session and o.available_at < cutoff.time}
    missing = [i for i in ids if i not in found]
    if missing:
        return gap(f"the value for {session} was not knowable before the cutoff: {', '.join(missing)}")
    return AsOfResult(cutoff=cutoff, series_ids=ids, status="requested", session=session,
                      observations=tuple(found[i] for i in ids))


def trailing_window(binding: SeriesBinding, observations: list[Observation], cutoff: Cutoff,
                    calendar: SessionCalendar, sessions: int, max_age: timedelta,
                    session: date | None = None) -> WindowedAsOf:
    """The ``sessions`` expected sessions ending at the latest one usable before the cutoff.

    The end is chosen by ``latest_common``, so the age limit and the skip rules are the same
    as for a single value; or it is the named ``session``, chosen by ``at_session``. Every
    session of the window must then have a value knowable before the cutoff; one missing
    makes the window incomplete, never shorter.
    """
    if session is not None:
        anchor = at_session([binding], observations, cutoff, calendar, session, max_age)
    else:
        anchor, _ = latest_common([binding], observations, cutoff, calendar, max_age)
    if anchor.status == "gap":
        return WindowedAsOf(anchor=anchor, sessions=sessions)
    end = calendar.sessions.index(anchor.session) + 1
    if end < sessions:
        return WindowedAsOf(anchor=anchor, sessions=sessions,
                            reason=f"the calendar holds {end} sessions up to {anchor.session}, fewer than {sessions}")
    usable = {o.session: o for o in as_of_vintage(observations, cutoff, (binding.series_id,))
              if o.available_at < cutoff.time}
    wanted = calendar.sessions[end - sessions:end]
    missing = [s for s in wanted if s not in usable]
    if missing:
        return WindowedAsOf(anchor=anchor, sessions=sessions,
                            reason=f"{len(missing)} of the {sessions} sessions from {wanted[0]} have no value "
                                   f"knowable before the cutoff, the latest being {missing[-1]}")
    return WindowedAsOf(anchor=anchor, sessions=sessions, observations=tuple(usable[s] for s in wanted))


def first_session_after(binding: SeriesBinding, calendar: SessionCalendar, cutoff: Cutoff) -> date | None:
    """The first expected session whose value for ``binding`` is fixed strictly after the cutoff."""
    return next((s for s in calendar.sessions if fixed_at(binding, s) > cutoff.time), None)


def session_offset(calendar: SessionCalendar, session: date, offset: int) -> date | None:
    """The expected session ``offset`` sessions after (or before) ``session``; None outside the calendar."""
    index = calendar.sessions.index(session) + offset
    return calendar.sessions[index] if 0 <= index < len(calendar.sessions) else None


def on_calendar(observations: list[Observation], calendar: SessionCalendar) -> tuple[list[Observation], list[Observation]]:
    """Split rows inside the calendar's span into (on an expected session, off it).

    Some sources publish on days that are not sessions of the calendar in use: Cboe gives
    VIX values on US equity holidays. ``latest_common`` rejects such rows, so drop them
    here deliberately and keep the second list as the record of what was dropped.
    """
    expected = set(calendar.sessions)
    kept, dropped = [], []
    for item in observations:
        off = calendar.first <= item.session <= calendar.last and item.session not in expected
        (dropped if off else kept).append(item)
    return kept, dropped


def ratio_series(binding: SeriesBinding, numerator: list[Observation], denominator: list[Observation]) -> list[Observation]:
    """A derived series: numerator over denominator on each session both have, knowable when the later of the two is.

    ``binding`` names the derived series. Each input list holds one series, one row per
    session. The quotient is ``assumed`` if either input's time is, and inherits no vintage:
    give it inputs already selected for the vintage wanted.
    """
    below = {o.session: o for o in denominator}
    if len(below) != len(denominator) or len({o.session for o in numerator}) != len(numerator):
        raise ValueError("each input must hold one row per session")
    out = []
    for top in numerator:
        bottom = below.get(top.session)
        if bottom is None or bottom.value == 0:
            continue
        basis = "assumed" if "assumed" in (top.availability_basis, bottom.availability_basis) else top.availability_basis
        out.append(Observation(series_id=binding.series_id, session=top.session, value=top.value / bottom.value,
                               available_at=max(top.available_at, bottom.available_at), availability_basis=basis,
                               source_url=binding.source_url))
    return out
