"""Contracts for evaluation output: one row per event, and the table that holds them.

DRAFT. A row reports what was knowable before an event and what happened after it. It
makes no claim that one explains the other.
"""

from datetime import date
from typing import Literal, Self

from pydantic import Field, computed_field, model_validator

from quant_contracts.pit import AsOfResult, Cutoff
from quant_contracts.research import Contract, PriorStatus, Text


class ReturnPair(Contract):
    """Simple returns of the subject and the benchmark over the same span."""

    subject: float
    benchmark: float

    @computed_field
    @property
    def relative(self) -> float:
        """Subject minus benchmark. A relative return: no beta adjustment is applied."""
        return self.subject - self.benchmark


class Factor(Contract):
    """One named measure of market state, built from bound series.

    A factor covers only the events for which its series existed. A stand-in used for an
    earlier period is a different factor with a different id, never the same one.
    """

    factor_id: Text
    measure: Literal["level", "ratio"]  # level: one series; ratio: numerator over denominator
    series_ids: tuple[Text, ...]
    max_age_days: int = Field(gt=0)  # how old the selected session may be at the cutoff

    @model_validator(mode="after")
    def series_match_measure(self) -> Self:
        if len(self.series_ids) != (1 if self.measure == "level" else 2) or len(set(self.series_ids)) != len(self.series_ids):
            raise ValueError("a level takes one series and a ratio two different ones")
        return self


class FactorState(Contract):
    """One factor as it was knowable before one cutoff: a value, or the reason there is none.

    There is no value when the state is a gap, or when inputs were found but the measure is
    undefined for them (a ratio with a zero denominator). The second case keeps the inputs
    in ``state`` and says why in ``reason``.
    """

    factor_id: Text
    state: AsOfResult  # the latest inputs knowable before the cutoff
    value: float | None
    reason: Text | None = None  # why inputs that were found gave no value

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        if self.state.status == "gap" and (self.value is not None or self.reason is not None):
            raise ValueError("a gap has no value; its reason is in the state")
        if self.state.status != "gap" and (self.value is None) == (self.reason is None):
            raise ValueError("inputs that were found give a value, or a reason there is none")
        return self


class EventOutcomeRow(Contract):
    """One event: its cutoff, the factors knowable before it, and the returns after it.

    ``prior_status`` and ``timing_verified`` are separate. The first says whether the event
    predates the frozen research map. The second is True only when the cutoff is exact, at
    least one factor has a value, and every factor with a value is verified; when False the
    row holds under the stated timing assumptions only. It covers the cutoff and the factor
    inputs. It does not cover the price series behind the returns, whose publication delays
    may be assumed.
    """

    event_id: Text
    cutoff: Cutoff
    factors: tuple[FactorState, ...]  # one per factor of the table, in the table's order
    prior_status: PriorStatus
    timing_verified: bool
    status: Literal["complete", "incomplete"]
    entry_session: date | None = None  # first session opening after the cutoff
    exit_session: date | None = None  # last session of the window
    reaction: ReturnPair | None = None  # previous close to entry open: the move already in the entry price
    window: ReturnPair | None = None  # entry open to exit close
    flags: tuple[Text, ...] = ()
    reason: Text | None = None  # set when incomplete


class EventTable(Contract):
    """The reproducible output of one recipe on one data snapshot."""

    recipe_id: Text
    recipe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_sha256: dict[str, str]  # file name to hash, for every input file
    definition: Text  # the outcome window, fixed before the data was read
    factors: tuple[Factor, ...]
    rows: tuple[EventOutcomeRow, ...]

    @model_validator(mode="after")
    def rows_carry_every_factor(self) -> Self:
        ids = tuple(f.factor_id for f in self.factors)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate factor id")
        if any(tuple(s.factor_id for s in row.factors) != ids for row in self.rows):
            raise ValueError("every row must carry the table's factors, in order")
        return self

    def coverage(self, factor_id: str) -> tuple[Text, ...]:
        """The events for which the factor has a value: the only rows it may be judged on."""
        return tuple(row.event_id for row in self.rows
                     for state in row.factors if state.factor_id == factor_id and state.value is not None)


class GroupComparison(Contract):
    """Two groups of one outcome, compared by their means. A description, not a verdict."""

    label: Text
    n_a: int
    n_b: int
    mean_a: float | None
    mean_b: float | None
    difference: float | None  # mean_a minus mean_b
    standard_error: float | None  # unequal-variance (Welch); None when a group has fewer than two values
