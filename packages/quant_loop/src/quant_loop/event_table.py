"""Run one recipe over dated events: state before each cutoff, returns after it.

Thin orchestration. Selection of sessions is ``quant_pit_data``; arithmetic is ``quant_evaluator``.
"""

from bisect import bisect_left, bisect_right
from datetime import date, timedelta, timezone
import hashlib
from zoneinfo import ZoneInfo

from quant_contracts.evaluation import EventOutcomeRow, EventTable, EventTime, Factor, FactorState
from quant_contracts.pit import Cutoff, Observation, SeriesBinding, SessionCalendar
from quant_contracts.research import CandidateFactorRecipe
from quant_evaluator.alpha.event_window import beta_before, return_pair
from quant_evaluator.core.rolling import sessions_needed, windowed_value
from quant_pit_data.asof import at_session, first_session_after, fixed_at, latest_common, session_offset, trailing_window

DEFINITION = ("Entry is the open of the first US regular session that opens after the cutoff. Exit is the close of "
              "session number N counting the entry session as 1, with N the recipe's horizon. window is the simple "
              "return from entry open to exit close; reaction is the simple return from the previous session's close "
              "to the entry open. relative is subject minus benchmark with no beta adjustment; a table built without a "
              "benchmark has the subject's returns only. Prices are on the "
              "snapshot's split basis and exclude dividends; a row is flagged when an ex-dividend or split date falls in its span, "
              "and when its cutoff falls at or after the open of a session, since an upper-bound cutoff that late means "
              "the event may already have been traded on. Factors are read before the state cutoff, which is the cutoff "
              "itself unless the cutoff is only an upper bound on the event's time; then it is a separate time no later "
              "than the event. Each factor is the latest value knowable before the state cutoff and no older than its own "
              "age limit; otherwise it is a recorded gap and the row keeps its returns. A ratio whose denominator is zero "
              "has no value and keeps its inputs. A measure over a lookback reads every expected session of its window; "
              "if one has no value knowable before the state cutoff the factor has no value, and the window is never "
              "shortened. Where an event names the session a factor must be read from, that session is looked up directly "
              "under the same cutoff and age limit, and no other session is used in its place.")


class SeriesIndex:
    """Observations of each series in session order, so one decision reads only its recent ones."""

    def __init__(self, observations: list[Observation]):
        self.by_series: dict[str, list[Observation]] = {}
        for item in observations:
            self.by_series.setdefault(item.series_id, []).append(item)
        for items in self.by_series.values():
            items.sort(key=lambda o: o.session)
            if any(a.session == b.session for a, b in zip(items, items[1:])):
                raise ValueError(f"duplicate observation for {items[0].series_id}")
        self.sessions = {i: [o.session for o in items] for i, items in self.by_series.items()}

    def reachable(self, series_id: str, cutoff: Cutoff, calendar: SessionCalendar, limit: timedelta,
                  sessions: int) -> list[Observation]:
        """Every observation the selector could use for this cutoff, and none it could not.

        The selector never picks a session older than ``limit`` before the cutoff, and a
        window reaches ``sessions`` calendar sessions back from the one it picks. So the
        slice runs from that many sessions before the oldest pickable one to the day after
        the cutoff, with a day's margin at each end for timezones. It is by session date,
        never by count: a value published late is still inside it. The result is the same
        as reading the whole history; only rows on days outside the slice go unchecked.
        """
        items, days = self.by_series.get(series_id, []), self.sessions.get(series_id, [])
        moment = cutoff.time.astimezone(timezone.utc)
        oldest = bisect_left(calendar.sessions, (moment - limit).date() - timedelta(days=1))
        first = calendar.sessions[oldest - sessions] if oldest >= sessions else date.min
        return items[bisect_left(days, first):bisect_right(days, moment.date() + timedelta(days=1))]


def factor_states(factors: list[Factor], bindings: dict[str, SeriesBinding], index: SeriesIndex, state_cutoff: Cutoff,
                  calendar: SessionCalendar, series_calendars: dict[str, SessionCalendar],
                  required_sessions: dict[str, date] | None = None) -> tuple[FactorState, ...]:
    """Every factor as knowable before ``state_cutoff``, each independent of the others.

    A series is read on its own calendar when ``series_calendars`` names one, otherwise on
    ``calendar``. So a lookback counts that series' own sessions: a day on which its market
    was closed is neither a missing value nor a repeated one. ``required_sessions`` names, per
    factor id, the one session its value must come from: that session is looked up directly,
    under the same cutoff and age limit, and no other session stands in for it.
    """
    required_sessions = required_sessions or {}
    if set(required_sessions) - {f.factor_id for f in factors}:
        raise ValueError("a required session names a factor the table does not have")
    states = []
    for factor in factors:
        limit, value, reason, first, inputs = timedelta(days=factor.max_age_days), None, None, None, None
        own = [series_calendars.get(i, calendar) for i in factor.series_ids]
        wanted = required_sessions.get(factor.factor_id)
        if factor.measure == "ratio":
            if own[0] != own[1]:
                raise ValueError(f"{factor.factor_id}: the two series of a ratio must share a calendar")
            pair = [bindings[i] for i in factor.series_ids]
            rows = [o for i in factor.series_ids for o in index.reachable(i, state_cutoff, own[0], limit, 1)]
            selected = (at_session(pair, rows, state_cutoff, own[0], wanted, limit) if wanted
                        else latest_common(pair, rows, state_cutoff, own[0], limit)[0])
            picked = {o.series_id: o.value for o in selected.observations}
            if picked and picked[factor.series_ids[1]] == 0:
                reason = f"ratio undefined: {factor.series_ids[1]} is zero on {selected.session}"
            elif picked:
                value = picked[factor.series_ids[0]] / picked[factor.series_ids[1]]
        else:
            needed = sessions_needed(factor.measure, factor.lookback_sessions, factor.rank_sessions)
            window = trailing_window(bindings[factor.series_ids[0]],
                                     index.reachable(factor.series_ids[0], state_cutoff, own[0], limit, needed),
                                     state_cutoff, own[0], needed, limit, wanted)
            selected, reason = window.anchor, window.reason
            if window.observations:
                first, inputs = (window.observations[0].session if needed > 1 else None), window.observations
                try:
                    value = windowed_value(factor.measure, factor.lookback_sessions, factor.rank_sessions,
                                           [o.value for o in window.observations])
                except ValueError as error:
                    reason = f"{factor.measure} undefined: {error}"
        assumed = sum(o.availability_basis == "assumed" for o in inputs or selected.observations)
        states.append(FactorState(factor_id=factor.factor_id, state=selected, value=value, reason=reason,
                                  window_first=first, assumed_inputs=assumed))
    return tuple(states)


def build_table(recipe: CandidateFactorRecipe, events: list[EventTime], factors: list[Factor],
                bindings: dict[str, SeriesBinding], subject: tuple[SeriesBinding, SeriesBinding],
                benchmark: tuple[SeriesBinding, SeriesBinding] | None, observations: list[Observation],
                calendar: SessionCalendar, corporate_actions: dict[str, list[date]],
                snapshot_sha256: dict[str, str],
                series_calendars: dict[str, SessionCalendar] | None = None,
                beta_sessions: int | None = None) -> EventTable:
    """``subject`` and ``benchmark`` are (open, close) bindings; ``bindings`` holds every factor series by id.

    ``benchmark`` is None when the recipe names none: the rows then hold the subject's own
    returns and no relative return.

    Factors are independent of each other and of the returns: one without data before the
    state cutoff is a gap on that row and nothing else changes. ``corporate_actions`` maps a label
    such as "ex-dividend TSM" to dates; a row is flagged for each date inside its span.
    ``series_calendars`` gives a factor series its own calendar; returns always use ``calendar``.
    ``beta_sessions`` adds a pre-event beta to each row: the subject's beta to the benchmark over
    that many close-to-close returns ending at the session before the entry, so it reads
    nothing from the row's own window. Where that history is incomplete the row has no beta.
    """
    index = SeriesIndex(observations)
    prices = {(o.series_id, o.session): o.value for o in observations}
    horizon = recipe.horizons_sessions[0]
    rows = []
    for event in events:
        event_id, cutoff = event.event_id, event.cutoff
        states = factor_states(factors, bindings, index, event.state, calendar, series_calendars or {},
                               event.required_sessions)
        usable = [s for s in states if s.value is not None]
        row = {"event_id": event_id, "cutoff": cutoff, "state_cutoff": event.state, "factors": states,
               "timing_verified": cutoff.basis == "exact" and bool(usable) and all(s.verified for s in usable),
               "prior_status": ("exploratory" if cutoff.time < recipe.prior_frozen_at
                                else "prior_available_at_decision")}
        entry = first_session_after(subject[0], calendar, cutoff)
        previous = session_offset(calendar, entry, -1) if entry else None
        last = session_offset(calendar, entry, horizon - 1) if entry else None
        flags = []
        if previous == cutoff.time.astimezone(ZoneInfo(calendar.timezone)).date() and fixed_at(subject[0], previous) <= cutoff.time:
            flags.append(f"cutoff at or after the open of {previous}")
        needed = [(binding.series_id, session) for pair in ((subject, benchmark) if benchmark else (subject,))
                  for binding, session in ((pair[1], previous), (pair[0], entry), (pair[1], last))]
        missing = [f"{series} on {session}" for series, session in needed
                   if session is None or (series, session) not in prices]
        if missing:
            rows.append(EventOutcomeRow(**row, status="incomplete", entry_session=entry, exit_session=last,
                                        flags=tuple(flags), reason="no price for " + "; ".join(missing)))
            continue
        s_prev, s_open, s_last = (prices[k] for k in needed[:3])
        b_prev, b_open, b_last = (prices[k] for k in needed[3:]) if benchmark else (None, None, None)
        beta = None
        if beta_sessions and benchmark:
            end = calendar.sessions.index(previous)
            history = calendar.sessions[max(0, end - beta_sessions):end + 1]
            beta = beta_before([prices.get((subject[1].series_id, s)) for s in history],
                               [prices.get((benchmark[1].series_id, s)) for s in history], len(history) - 1, beta_sessions)
            if beta is None:
                flags.append(f"no pre-event beta: fewer than {beta_sessions} complete returns before {entry}")
        flags += [f"{label} {day}" for label, days in sorted(corporate_actions.items())
                  for day in days if previous < day <= last]
        rows.append(EventOutcomeRow(**row, status="complete", entry_session=entry, exit_session=last,
                                    reaction=return_pair(s_prev, s_open, b_prev, b_open, beta),
                                    window=return_pair(s_open, s_last, b_open, b_last, beta), flags=tuple(flags)))
    digest = hashlib.sha256(recipe.model_dump_json().encode("utf-8")).hexdigest()
    return EventTable(recipe_id=recipe.recipe_id, recipe_sha256=digest, snapshot_sha256=snapshot_sha256,
                      definition=DEFINITION, factors=tuple(factors), rows=tuple(rows))
