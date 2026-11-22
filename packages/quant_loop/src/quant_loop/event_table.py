"""Run one recipe over dated events: state before each cutoff, returns after it.

Thin orchestration. Selection of sessions is ``quant_pit_data``; arithmetic is ``quant_evaluator``.
"""

from datetime import date, timedelta
import hashlib

from quant_contracts.evaluation import EventOutcomeRow, EventTable
from quant_contracts.pit import Cutoff, Observation, SeriesBinding, SessionCalendar
from quant_contracts.research import CandidateFactorRecipe
from quant_evaluator.alpha.event_window import return_pair
from quant_pit_data.asof import first_session_after, latest_common, session_offset

DEFINITION = ("Entry is the open of the first US regular session that opens after the cutoff. Exit is the close of "
              "session number N counting the entry session as 1, with N the recipe's horizon. window is the simple "
              "return from entry open to exit close; reaction is the simple return from the previous session's close "
              "to the entry open. relative is subject minus benchmark with no beta adjustment. Prices are on the "
              "snapshot's split basis and exclude dividends; a row is flagged when an ex-dividend date falls in its span.")


def build_table(recipe: CandidateFactorRecipe, events: list[tuple[str, Cutoff]],
                state: tuple[SeriesBinding, SeriesBinding], subject: tuple[SeriesBinding, SeriesBinding],
                benchmark: tuple[SeriesBinding, SeriesBinding], observations: list[Observation],
                calendar: SessionCalendar, ex_dividends: dict[str, list[date]], max_age: timedelta,
                snapshot_sha256: dict[str, str]) -> EventTable:
    """``state`` is (numerator, denominator); ``subject`` and ``benchmark`` are (open, close) bindings."""
    prices: dict[tuple[str, date], float] = {}
    for item in observations:
        if (item.series_id, item.session) in prices:
            raise ValueError(f"duplicate observation for {item.series_id} on {item.session}")
        prices[(item.series_id, item.session)] = item.value
    horizon = recipe.horizons_sessions[0]
    rows = []
    for event_id, cutoff in events:
        selected, _ = latest_common(list(state), observations, cutoff, calendar, max_age)
        values = {o.series_id: o.value for o in selected.observations}
        ratio = values[state[0].series_id] / values[state[1].series_id] if values else None
        row = {"event_id": event_id, "cutoff": cutoff, "state": selected, "state_value": ratio,
               "timing_verified": selected.verified,
               "prior_status": ("exploratory" if cutoff.time < recipe.prior_frozen_at
                                else "prior_available_at_decision")}
        entry = first_session_after(subject[0], calendar, cutoff)
        previous = session_offset(calendar, entry, -1) if entry else None
        last = session_offset(calendar, entry, horizon - 1) if entry else None
        needed = [(binding.series_id, session) for pair in (subject, benchmark)
                  for binding, session in ((pair[1], previous), (pair[0], entry), (pair[1], last))]
        missing = [f"{series} on {session}" for series, session in needed
                   if session is None or (series, session) not in prices]
        if missing:
            rows.append(EventOutcomeRow(**row, status="incomplete", entry_session=entry, exit_session=last,
                                        reason="no price for " + "; ".join(missing)))
            continue
        (s_prev, s_open, s_last), (b_prev, b_open, b_last) = ([prices[k] for k in needed[i:i + 3]] for i in (0, 3))
        flags = [f"ex-dividend {name} {day}" for name, days in sorted(ex_dividends.items())
                 for day in days if previous < day <= last]
        rows.append(EventOutcomeRow(**row, status="complete", entry_session=entry, exit_session=last,
                                    reaction=return_pair(s_prev, s_open, b_prev, b_open),
                                    window=return_pair(s_open, s_last, b_open, b_last), flags=tuple(flags)))
    digest = hashlib.sha256(recipe.model_dump_json().encode("utf-8")).hexdigest()
    return EventTable(recipe_id=recipe.recipe_id, recipe_sha256=digest, snapshot_sha256=snapshot_sha256,
                      definition=DEFINITION, rows=tuple(rows))
