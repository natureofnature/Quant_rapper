"""Hand one generated candidate to the event table, checking that the pieces belong together.

A recipe is evaluated only if it is the very recipe the map generated for a candidate (by
hash, since ids repeat across maps), with that candidate's factor, at the map's own decision
time, and with bindings equal to the ones the map recorded. Realised returns
enter here and nowhere earlier.
"""

from datetime import date

from quant_causal_graph.causal_map import binding_digest, price_series, recipe_digest
from quant_contracts.causal_map import CausalMap
from quant_contracts.evaluation import EventTable, EventTime
from quant_contracts.pit import Cutoff, Observation, SeriesBinding, SessionCalendar
from quant_contracts.research import CandidateFactorRecipe
from quant_loop.event_table import build_table


def evaluate_candidate(causal_map: CausalMap, recipe: CandidateFactorRecipe, bindings: dict[str, SeriesBinding],
                       observations: list[Observation], calendar: SessionCalendar, corporate_actions: dict[str, list[date]],
                       snapshot_sha256: dict[str, str],
                       series_calendars: dict[str, SessionCalendar] | None = None) -> EventTable:
    """One row for the map's decision: the candidate's factor before it, the recipe's outcome after it.

    ``observations`` are the snapshot's series; the values the map's sources reported are
    added from the map itself, so the factor sees only what the map saw. The outcome is the
    recipe's: with no benchmark the row holds the subject's own return.
    """
    if (recipe.subject_id, recipe.decision_time) != (causal_map.subject_id, causal_map.decision_time):
        raise ValueError("the recipe is for another subject or another decision time than the map")
    candidate = next((c for c in causal_map.candidates if c.recipe_id == recipe.recipe_id), None)
    if candidate is None:
        raise ValueError(f"{recipe.recipe_id!r} is not the recipe of a measurable candidate of this map")
    if candidate.recipe_sha256 != recipe_digest(recipe):
        raise ValueError(f"{recipe.recipe_id!r} is not the recipe this map generated: same id, different content")
    factor = candidate.factor

    def pair(subject_id: str) -> tuple[SeriesBinding, SeriesBinding]:
        found = price_series(bindings, subject_id)
        if set(found) != {"open", "close"}:
            raise ValueError(f"no open and close series are bound for {subject_id}")
        return bindings[found["open"]], bindings[found["close"]]

    subject = pair(recipe.subject_id)
    benchmark = pair(recipe.benchmark) if recipe.benchmark else None
    used = [*factor.series_ids, *(b.series_id for b in subject), *(b.series_id for b in benchmark or ())]
    changed = sorted(i for i in used if causal_map.inputs_sha256.get(f"binding:{i}") != binding_digest(bindings[i]))
    if changed:
        raise ValueError(f"these bindings differ from the ones the map was generated with: {changed}")

    # The candidate's timing decides what the row is: the event's own content read for its period, or a state at
    # the decision time. A state row is never labelled with an event, even when one focuses the map.
    event = causal_map.event if candidate.timing == "event_content" else None
    required = {factor.factor_id: event.content_session} if event else {}
    when = Cutoff(time=causal_map.decision_time, basis="exact", source="The decision time the map was generated for")
    name = event.event_id if event else f"decision:{causal_map.subject_id}:{causal_map.decision_time.isoformat()}"
    return build_table(recipe, [EventTime(event_id=name, cutoff=when, required_sessions=required)], [factor],
                       {i: bindings[i] for i in factor.series_ids}, subject, benchmark,
                       list(causal_map.content) + observations, calendar, corporate_actions, snapshot_sha256,
                       series_calendars=series_calendars)
