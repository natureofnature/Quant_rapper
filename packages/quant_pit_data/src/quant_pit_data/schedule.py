"""Counting sessions to the next scheduled event, with the schedule as it stood on the day of the count."""

from datetime import date

from quant_contracts.pit import SessionCalendar


def scheduled_as_known(held: list[date], withdrawn: list[tuple[date, date]], on: date) -> list[date]:
    """The scheduled dates as they stood on ``on``, sorted.

    ``held`` are the scheduled events that took place. ``withdrawn`` are (scheduled date, day
    the withdrawal became known): such an event was still on the calendar before that day,
    so it is counted until then and dropped from it on.
    """
    return sorted({*held, *(scheduled for scheduled, known_on in withdrawn if on < known_on)})


def sessions_to_next(calendar: SessionCalendar, session: date, scheduled: list[date]) -> int | None:
    """Sessions from ``session`` to the next scheduled date, 0 on the day itself.

    None when no date is ahead, when ``session`` is not a session, or when the calendar ends
    before the next date: the sessions in between cannot then be counted, and counting only
    the ones the calendar holds would make a distant event look close.
    """
    ahead = next((d for d in sorted(scheduled) if d >= session), None)
    if ahead is None or session not in calendar.sessions or ahead > calendar.last:
        return None
    return sum(1 for s in calendar.sessions[calendar.sessions.index(session):] if s < ahead)
