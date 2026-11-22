"""Contracts for evaluation output: one row per event, and the table that holds them.

DRAFT. A row reports what was knowable before an event and what happened after it. It
makes no claim that one explains the other.
"""

from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, Field, computed_field, model_validator

from quant_contracts.pit import AsOfResult, Cutoff
from quant_contracts.research import Contract, PriorStatus, Text


class ReturnPair(Contract):
    """Simple returns of the subject and the benchmark over the same span.

    With no benchmark there is no relative return: the outcome is the subject's own return,
    and nothing is invented to subtract.
    """

    subject: float
    benchmark: float | None = None

    @model_validator(mode="before")
    @classmethod
    def saved_difference_must_match(cls, data):
        """A saved pair carries ``relative``; accept it on loading only if it is the difference."""
        if isinstance(data, dict) and "relative" in data:
            data = dict(data)
            expected = None if data.get("benchmark") is None else data.get("subject", 0.0) - data["benchmark"]
            if data.pop("relative") != expected:
                raise ValueError("relative is not subject minus benchmark")
        return data

    @computed_field
    @property
    def relative(self) -> float | None:
        """Subject minus benchmark, or None without a benchmark. No beta adjustment is applied."""
        return None if self.benchmark is None else self.subject - self.benchmark


class Factor(Contract):
    """One named measure of market state, built from bound series.

    A factor covers only the events for which its series existed. A stand-in used for an
    earlier period is a different factor with a different id, never the same one.
    """

    factor_id: Text
    # level: one series' latest value. ratio: numerator over denominator on one session.
    # change, roc, realized_vol, trend_state: one series over ``lookback_sessions``, as defined
    # in quant_evaluator.core.rolling.
    measure: Literal["level", "ratio", "change", "roc", "realized_vol", "trend_state"]
    series_ids: tuple[Text, ...]
    max_age_days: int = Field(gt=0)  # how old the selected session may be at the cutoff
    lookback_sessions: int | None = Field(default=None, gt=0)
    # When set, the value is the measure's rank among its own previous ``rank_sessions``
    # values (share strictly below, 0 to 1), not the measure itself.
    rank_sessions: int | None = Field(default=None, gt=1)

    @model_validator(mode="after")
    def series_match_measure(self) -> Self:
        if len(self.series_ids) != (2 if self.measure == "ratio" else 1) or len(set(self.series_ids)) != len(self.series_ids):
            raise ValueError("a ratio takes two different series and every other measure one")
        if (self.lookback_sessions is None) != (self.measure in ("level", "ratio")):
            raise ValueError("a level and a ratio take no lookback; every other measure needs one")
        if self.measure == "realized_vol" and self.lookback_sessions < 2:
            raise ValueError("realized volatility needs at least two returns")
        if self.measure == "ratio" and self.rank_sessions is not None:
            raise ValueError("a ratio cannot be ranked")
        return self


class FactorState(Contract):
    """One factor as it was knowable before one cutoff: a value, or the reason there is none.

    There is no value when the state is a gap, or when inputs were found but the measure is
    undefined for them (a ratio with a zero denominator, a window with a session missing).
    The second case keeps the inputs in ``state`` and says why in ``reason``.
    """

    factor_id: Text
    state: AsOfResult  # the latest inputs knowable before the cutoff
    value: float | None
    reason: Text | None = None  # why inputs that were found gave no value
    window_first: date | None = None  # first session read, when the measure reads more than one
    # Of all the observations the value was computed from, how many had an assumed
    # publication time. A window keeps only its last session in ``state``, so this is
    # where the earlier ones are accounted for.
    assumed_inputs: int = Field(default=0, ge=0)

    @property
    def verified(self) -> bool:
        """The cutoff is exact and no observation behind the value had an assumed publication time."""
        return self.state.verified and self.assumed_inputs == 0

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        if self.assumed_inputs < sum(o.availability_basis == "assumed" for o in self.state.observations):
            raise ValueError("assumed_inputs must count at least the assumed observations in the state")
        if self.state.status == "gap" and (self.value is not None or self.reason is not None):
            raise ValueError("a gap has no value; its reason is in the state")
        if self.state.status != "gap" and (self.value is None) == (self.reason is None):
            raise ValueError("inputs that were found give a value, or a reason there is none")
        return self


def check_cutoffs(cutoff: Cutoff, state: Cutoff) -> None:
    """The rules every pair of entry cutoff and state cutoff must meet, wherever it is stored."""
    if cutoff.basis == "lower_bound":
        raise ValueError("entry cannot follow a lower bound: the event may not have happened yet")
    if state.basis == "upper_bound":
        raise ValueError("pre-event state cannot be read before an upper bound: give a state cutoff "
                         "that is no later than the event")
    if state.time > cutoff.time:
        raise ValueError("the state cutoff cannot be after the cutoff")


class EventTime(Contract):
    """When an event became public, as two questions that an uncertain time answers differently.

    ``cutoff`` is the moment by which the event was public: the entry follows it. An upper
    bound is safe here, since one cannot trade on what is not yet known.
    ``state_cutoff`` is the moment before which the factors are read. An upper bound is not
    safe here: if the event came earlier, values published in between already reflect it. So
    an upper-bound cutoff needs a separate state cutoff that is no later than the event.
    Left out, the state is read before ``cutoff`` itself.
    """

    event_id: Text
    cutoff: Cutoff
    state_cutoff: Cutoff | None = None
    # factor id -> the session that factor must be read from for this event. For a factor that
    # is the event's own content (a reported figure), the latest value of another period is not
    # a stand-in: without the named session's value the factor has no value.
    required_sessions: dict[Text, date] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounds_point_the_safe_way(self) -> Self:
        check_cutoffs(self.cutoff, self.state)
        return self

    @property
    def state(self) -> Cutoff:
        return self.state_cutoff or self.cutoff


class EventOutcomeRow(Contract):
    """One event: its cutoffs, the factors knowable before it, and the returns after it.

    ``prior_status`` and ``timing_verified`` are separate. The first says whether the event
    predates the frozen research map. The second is True only when the cutoff is exact, at
    least one factor has a value, and every factor with a value is verified, counting every
    observation the value was computed from; when False the
    row holds under the stated timing assumptions only. It covers the cutoff and the factor
    inputs. It does not cover the price series behind the returns, whose publication delays
    may be assumed.
    """

    event_id: Text
    cutoff: Cutoff  # the event was public by this time; the entry follows it
    state_cutoff: Cutoff  # the factors were read before this time; equal to ``cutoff`` unless given
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

    @model_validator(mode="after")
    def row_is_consistent(self) -> Self:
        check_cutoffs(self.cutoff, self.state_cutoff)
        if any(s.state.cutoff != self.state_cutoff for s in self.factors):
            raise ValueError("every factor must be read before the row's state cutoff")
        usable = [s for s in self.factors if s.value is not None]
        if self.timing_verified != (self.cutoff.basis == "exact" and bool(usable) and all(s.verified for s in usable)):
            raise ValueError("timing_verified does not follow from the cutoff and the factors")
        if self.status == "complete":
            if None in (self.entry_session, self.exit_session, self.reaction, self.window) or self.reason is not None:
                raise ValueError("a complete row has its sessions and returns, and no reason")
        elif self.reason is None or self.reaction is not None or self.window is not None:
            raise ValueError("an incomplete row has a reason and no returns")
        if self.entry_session and self.exit_session and self.entry_session > self.exit_session:
            raise ValueError("the entry session cannot be after the exit session")
        return self


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


class PanelRow(Contract):
    """One subject at one decision: every feature as it was knowable then. Keyed (session, subject, as_of).

    A feature with no value is None and stays None; nothing is filled in. ``sources`` gives the
    session each value was read from, which can be earlier than ``session``. A row holds no
    outcome: targets are joined later, by the model's own purge rule.
    """

    session: date
    subject_id: Text
    as_of: AwareDatetime
    features: dict[Text, float | None]
    sources: dict[Text, date | None]
    assumed_inputs: dict[Text, int]  # per feature, how many observations behind it had an assumed publication time

    @model_validator(mode="after")
    def keys_agree(self) -> Self:
        if not (set(self.features) == set(self.sources) == set(self.assumed_inputs)):
            raise ValueError("features, sources and assumed_inputs must name the same features")
        return self


class GroupComparison(Contract):
    """Two groups of one outcome, compared by their means. A description, not a verdict."""

    label: Text
    n_a: int
    n_b: int
    mean_a: float | None
    mean_b: float | None
    difference: float | None  # mean_a minus mean_b
    standard_error: float | None  # unequal-variance (Welch); None when a group has fewer than two values
