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
                                 SessionCalendar, SkippedSession)


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


def latest_common(bindings: list[SeriesBinding], observations: list[Observation], cutoff: Cutoff,
                  calendar: SessionCalendar, max_age: timedelta) -> tuple[AsOfResult, tuple[AuditNote, ...]]:
    """Pick the latest expected session for which every series was knowable before the cutoff.

    Returns (result, audit). The result is decision-facing: a skipped session lists the
    series that were unavailable as of the cutoff and nothing else. The audit notes say
    why, and may hold later knowledge such as when a late value was finally published.

    Expected sessions come from ``calendar``, so a session missing from every series is a
    reported skip, not a silent holiday. ``max_age`` bounds how old the chosen close may be.
    """
    zone = ZoneInfo(calendar.timezone)
    if not calendar.first <= cutoff.time.astimezone(zone).date() <= calendar.last:
        raise ValueError(f"calendar {calendar.calendar_id!r} does not cover the cutoff")
    by_id = {b.series_id: b for b in bindings}
    ids, expected = tuple(by_id), set(calendar.sessions)
    table: dict[tuple[date, str], Observation] = {}
    for item in observations:
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
        age = cutoff.time - max(fixed_at(b, session) for b in bindings)
        if age > max_age:
            return result(status="gap", reason=f"latest usable session {session} is {age} old, over the limit of {max_age}")
        return result(status="fallback" if skipped else "resolved", session=session,
                      observations=tuple(table[(session, i)] for i in ids))
    return result(status="gap", reason="no expected session with every series available before the cutoff")


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
    inside = [o for o in observations if calendar.first <= o.session <= calendar.last]
    return ([o for o in observations if o not in inside or o.session in expected],
            [o for o in inside if o.session not in expected])
