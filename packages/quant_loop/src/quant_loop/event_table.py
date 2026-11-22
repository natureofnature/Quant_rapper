"""Run one recipe over dated events: state before each cutoff, returns after it.

Thin orchestration. Selection of sessions is ``quant_pit_data``; arithmetic is ``quant_evaluator``.
"""

from datetime import date, timedelta
import hashlib
from zoneinfo import ZoneInfo

from quant_contracts.evaluation import EventOutcomeRow, EventTable, EventTime, Factor, FactorState
from quant_contracts.pit import Observation, SeriesBinding, SessionCalendar
from quant_contracts.research import CandidateFactorRecipe
from quant_evaluator.alpha.event_window import return_pair
from quant_evaluator.core.rolling import sessions_needed, windowed_value
from quant_pit_data.asof import first_session_after, fixed_at, latest_common, session_offset, trailing_window

DEFINITION = ("Entry is the open of the first US regular session that opens after the cutoff. Exit is the close of "
              "session number N counting the entry session as 1, with N the recipe's horizon. window is the simple "
              "return from entry open to exit close; reaction is the simple return from the previous session's close "
              "to the entry open. relative is subject minus benchmark with no beta adjustment. Prices are on the "
              "snapshot's split basis and exclude dividends; a row is flagged when an ex-dividend or split date falls in its span, "
              "and when its cutoff falls at or after the open of a session, since an upper-bound cutoff that late means "
              "the event may already have been traded on. Factors are read before the state cutoff, which is the cutoff "
              "itself unless the cutoff is only an upper bound on the event's time; then it is a separate time no later "
              "than the event. Each factor is the latest value knowable before the state cutoff and no older than its own "
              "age limit; otherwise it is a recorded gap and the row keeps its returns. A ratio whose denominator is zero "
              "has no value and keeps its inputs. A measure over a lookback reads every expected session of its window; "
              "if one has no value knowable before the state cutoff the factor has no value, and the window is never "
              "shortened.")


def build_table(recipe: CandidateFactorRecipe, events: list[EventTime], factors: list[Factor],
                bindings: dict[str, SeriesBinding], subject: tuple[SeriesBinding, SeriesBinding],
                benchmark: tuple[SeriesBinding, SeriesBinding], observations: list[Observation],
                calendar: SessionCalendar, corporate_actions: dict[str, list[date]],
                snapshot_sha256: dict[str, str]) -> EventTable:
    """``subject`` and ``benchmark`` are (open, close) bindings; ``bindings`` holds every factor series by id.

    Factors are independent of each other and of the returns: one without data before the
    state cutoff is a gap on that row and nothing else changes. ``corporate_actions`` maps a label
    such as "ex-dividend TSM" to dates; a row is flagged for each date inside its span.
    """
    prices: dict[tuple[str, date], float] = {}
    by_series: dict[str, list[Observation]] = {}
    for item in observations:
        if (item.series_id, item.session) in prices:
            raise ValueError(f"duplicate observation for {item.series_id} on {item.session}")
        prices[(item.series_id, item.session)] = item.value
        by_series.setdefault(item.series_id, []).append(item)
    horizon = recipe.horizons_sessions[0]
    rows = []
    for event in events:
        event_id, cutoff = event.event_id, event.cutoff
        states = []
        for factor in factors:
            limit, value, reason, first, inputs = timedelta(days=factor.max_age_days), None, None, None, None
            if factor.measure == "ratio":
                selected, _ = latest_common([bindings[i] for i in factor.series_ids],
                                            [o for i in factor.series_ids for o in by_series.get(i, ())],
                                            event.state, calendar, limit)
                picked = {o.series_id: o.value for o in selected.observations}
                if picked and picked[factor.series_ids[1]] == 0:
                    reason = f"ratio undefined: {factor.series_ids[1]} is zero on {selected.session}"
                elif picked:
                    value = picked[factor.series_ids[0]] / picked[factor.series_ids[1]]
            else:
                needed = sessions_needed(factor.measure, factor.lookback_sessions, factor.rank_sessions)
                window = trailing_window(bindings[factor.series_ids[0]], by_series.get(factor.series_ids[0], []),
                                         event.state, calendar, needed, limit)
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
        usable = [s for s in states if s.value is not None]
        row = {"event_id": event_id, "cutoff": cutoff, "state_cutoff": event.state, "factors": tuple(states),
               "timing_verified": cutoff.basis == "exact" and bool(usable) and all(s.verified for s in usable),
               "prior_status": ("exploratory" if cutoff.time < recipe.prior_frozen_at
                                else "prior_available_at_decision")}
        entry = first_session_after(subject[0], calendar, cutoff)
        previous = session_offset(calendar, entry, -1) if entry else None
        last = session_offset(calendar, entry, horizon - 1) if entry else None
        flags = []
        if previous == cutoff.time.astimezone(ZoneInfo(calendar.timezone)).date() and fixed_at(subject[0], previous) <= cutoff.time:
            flags.append(f"cutoff at or after the open of {previous}")
        needed = [(binding.series_id, session) for pair in (subject, benchmark)
                  for binding, session in ((pair[1], previous), (pair[0], entry), (pair[1], last))]
        missing = [f"{series} on {session}" for series, session in needed
                   if session is None or (series, session) not in prices]
        if missing:
            rows.append(EventOutcomeRow(**row, status="incomplete", entry_session=entry, exit_session=last,
                                        flags=tuple(flags), reason="no price for " + "; ".join(missing)))
            continue
        (s_prev, s_open, s_last), (b_prev, b_open, b_last) = ([prices[k] for k in needed[i:i + 3]] for i in (0, 3))
        flags += [f"{label} {day}" for label, days in sorted(corporate_actions.items())
                  for day in days if previous < day <= last]
        rows.append(EventOutcomeRow(**row, status="complete", entry_session=entry, exit_session=last,
                                    reaction=return_pair(s_prev, s_open, b_prev, b_open),
                                    window=return_pair(s_open, s_last, b_open, b_last), flags=tuple(flags)))
    digest = hashlib.sha256(recipe.model_dump_json().encode("utf-8")).hexdigest()
    return EventTable(recipe_id=recipe.recipe_id, recipe_sha256=digest, snapshot_sha256=snapshot_sha256,
                      definition=DEFINITION, factors=tuple(factors), rows=tuple(rows))
