"""The feature panel: one row per subject and decision, built from the same point-in-time selection as everything else.

Thin orchestration. The panel carries features only. Outcomes are joined by the model's
own purge rule, so a panel can be built and inspected without revealing any result.
"""

from datetime import date, time

from quant_contracts.evaluation import Factor, PanelRow
from quant_contracts.pit import Observation, SeriesBinding, SessionCalendar
from quant_loop.daily_states import daily_states


def feature_panel(subject_id: str, sessions: list[date], decision_time: time, factors: list[Factor],
                  bindings: dict[str, SeriesBinding], observations: list[Observation], calendar: SessionCalendar,
                  series_calendars: dict[str, SessionCalendar] | None = None) -> list[PanelRow]:
    """One row per session for ``subject_id``, each factor read before ``decision_time`` on that session.

    A factor with no value at a decision is None in that row. Nothing is carried forward
    here beyond what the factor's own age limit allows, and nothing is filled.
    """
    rows = []
    for (cutoff, states), session in zip(daily_states(sessions, decision_time, factors, bindings, observations, calendar,
                                                      series_calendars), sessions):
        usable = {s.factor_id: s for s in states}
        rows.append(PanelRow(
            session=session, subject_id=subject_id, as_of=cutoff.time,
            features={i: s.value for i, s in usable.items()},
            sources={i: s.state.session if s.value is not None else None for i, s in usable.items()},
            assumed_inputs={i: s.assumed_inputs for i, s in usable.items()}))
    return rows
