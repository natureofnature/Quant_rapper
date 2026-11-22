"""Factor states at a fixed time after each session: the inputs of a daily rule.

Thin orchestration over the same selection the event table uses.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from quant_contracts.evaluation import Factor, FactorState
from quant_contracts.pit import Cutoff, Observation, SeriesBinding, SessionCalendar
from quant_loop.event_table import SeriesIndex, factor_states


def daily_states(sessions: list[date], decision_time: time, factors: list[Factor], bindings: dict[str, SeriesBinding],
                 observations: list[Observation], calendar: SessionCalendar,
                 series_calendars: dict[str, SessionCalendar] | None = None) -> list[tuple[Cutoff, tuple[FactorState, ...]]]:
    """For each session, every factor as knowable before ``decision_time`` on that date in the calendar's timezone.

    The cutoff is exact: it is the rule's own decision time, not an estimate of someone else's.
    """
    index, zone = SeriesIndex(observations), ZoneInfo(calendar.timezone)
    out = []
    for session in sessions:
        cutoff = Cutoff(time=datetime.combine(session, decision_time, zone), basis="exact",
                        source="The rule's own decision time")
        out.append((cutoff, factor_states(factors, bindings, index, cutoff, calendar, series_calendars or {})))
    return out
