"""Contracts for point-in-time observations: series bindings, observations, as-of results.

DRAFT. A binding says where a series comes from and when each session's value becomes
knowable. An observation is one value with the moment it became available. An as-of
result is the answer to "what was the latest value knowable before this cutoff?".

Every time in an as-of result carries its basis, so "resolved" always means resolved
under the stated cutoff and availability assumptions, never verified by itself.
"""

from datetime import date, time
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from quant_contracts.research import Availability, Contract, Text

# exact: a recorded first-publication time. upper_bound: the true time was no later.
# lower_bound: the true time was no earlier. assumed: a convention with no record behind it.
CutoffBasis = Literal["exact", "upper_bound", "lower_bound", "assumed"]
# sourced / assumed: from the binding's rule. recorded: this observation's own recorded time.
AvailabilityBasis = Literal["sourced", "assumed", "recorded"]


class SeriesBinding(Contract):
    """Binds an observable named in the research map to one concrete daily series.

    One binding is one value per session, so a session's open and close are two bindings.
    """

    series_id: Text  # e.g. "cboe:vix"
    label: Text
    anchors: tuple[Text, ...] = Field(min_length=1)  # concept, company or fund ids it serves
    source_url: Text
    availability: Availability
    timezone: Text  # IANA name of the session's local time, e.g. "America/New_York"
    value_time: time  # local time at which the session's value is fixed: the open or the close
    publication_delay_minutes: int = Field(ge=0)  # from that time to knowable
    availability_basis: Literal["sourced", "assumed"]  # whether value_time plus delay is documented
    revisable: bool  # True when published values can later be restated
    basis_note: Text  # where value_time and the delay come from


class EventBinding(Contract):
    """Where the timestamp of a recurring announcement is taken from."""

    event_id: Text  # e.g. "tsmc:monthly_revenue"
    label: Text
    anchors: tuple[Text, ...] = Field(min_length=1)
    timestamp_source: Text  # which record supplies the time
    timezone: Text
    bound: CutoffBasis
    basis_note: Text


class SessionCalendar(Contract):
    """The sessions expected between ``first`` and ``last``, both inclusive.

    It lets a session missing from every series be told apart from a holiday.
    """

    calendar_id: Text
    timezone: Text
    first: date
    last: date
    sessions: tuple[date, ...] = Field(min_length=1)
    basis_note: Text

    @model_validator(mode="after")
    def sessions_are_ordered_and_inside(self) -> Self:
        if list(self.sessions) != sorted(set(self.sessions)):
            raise ValueError("sessions must be unique and ascending")
        if self.sessions[0] < self.first or self.sessions[-1] > self.last:
            raise ValueError("sessions fall outside first..last")
        return self


class Cutoff(Contract):
    """The moment before which an input must have been knowable, and how well it is known."""

    time: AwareDatetime
    basis: CutoffBasis
    source: Text


class Observation(Contract):
    """One session's value and the moment it became knowable."""

    series_id: Text
    session: date
    value: float
    available_at: AwareDatetime
    availability_basis: AvailabilityBasis
    source_url: Text
    # For a series whose published values can be revised: when this particular value was published.
    # None means the value is the one visible when the snapshot was taken, which may be a later revision.
    vintage_at: AwareDatetime | None = None


def known_before(observation: Observation, cutoff: Cutoff) -> bool:
    """Whether this version of the value was public before the cutoff: both its release and, if revised, its vintage."""
    return observation.available_at < cutoff.time and (observation.vintage_at is None or observation.vintage_at < cutoff.time)


class SkippedSession(Contract):
    """An expected session that could not be used.

    Decision-facing: it states only what was knowable at the cutoff, never when a late
    value eventually appeared.
    """

    session: date
    unavailable: tuple[Text, ...] = Field(min_length=1)  # series ids unavailable as of the cutoff


class AsOfResult(Contract):
    """The latest expected session whose values, for every series, were knowable before the cutoff.

    status:
      resolved  - the chosen session is the latest expected one that had closed before the cutoff.
      fallback  - a later expected session was unavailable for some series; see ``skipped``.
      requested - the caller named the session; later ones were not considered.
      gap       - no usable session, or the only one is older than the staleness limit.

    Safe to show to a research step acting at the cutoff: it holds nothing learned later.
    """

    cutoff: Cutoff
    series_ids: tuple[Text, ...] = Field(min_length=1)
    status: Literal["resolved", "fallback", "requested", "gap"]
    session: date | None = None
    observations: tuple[Observation, ...] = ()
    skipped: tuple[SkippedSession, ...] = ()
    reason: Text | None = None  # set for a gap

    @model_validator(mode="after")
    def check_selected_session(self) -> Self:
        if self.status == "gap":
            if self.session is not None or self.observations or self.reason is None:
                raise ValueError("a gap has a reason, and no session or observations")
            return self
        if self.reason is not None:
            raise ValueError("reason is only for a gap")
        if (self.status == "fallback") != bool(self.skipped):
            raise ValueError("a fallback lists what it skipped, and a resolved result skipped nothing")
        if any(not known_before(o, self.cutoff) for o in self.observations):
            raise ValueError("every observation must have been available, in that version, strictly before the cutoff")
        if self.session is None or any(o.session != self.session for o in self.observations):
            raise ValueError("resolved and fallback observations must belong to the selected session")
        observed_ids = {o.series_id for o in self.observations}
        if observed_ids != set(self.series_ids) or len(observed_ids) != len(self.observations):
            raise ValueError("expected exactly one observation for each requested series")
        return self

    @property
    def verified(self) -> bool:
        """Verify only a resolved or requested session with an exact cutoff and documented availability."""
        return (self.status in ("resolved", "requested") and self.cutoff.basis == "exact"
                and all(o.availability_basis != "assumed" for o in self.observations))


class WindowedAsOf(Contract):
    """The trailing ``sessions`` expected sessions of one series, every one knowable before the cutoff.

    ``anchor`` is the latest usable session and carries the cutoff, the age limit and what
    was skipped. The window is the expected sessions ending there. It is never shortened:
    if any of them has no value knowable before the cutoff, there are no observations and
    ``reason`` says which.
    """

    anchor: AsOfResult
    sessions: int = Field(gt=0)
    observations: tuple[Observation, ...] = ()  # ascending by session, ending at the anchor's
    reason: Text | None = None  # set when the anchor was found but the window is incomplete

    @model_validator(mode="after")
    def check_window(self) -> Self:
        if len(self.anchor.series_ids) != 1:
            raise ValueError("a window holds one series")
        if self.anchor.status == "gap":
            if self.observations or self.reason is not None:
                raise ValueError("a gap has no window; its reason is in the anchor")
            return self
        if (self.reason is None) == (not self.observations):
            raise ValueError("a window has its observations, or the reason it is incomplete")
        if self.observations:
            days = [o.session for o in self.observations]
            if len(days) != self.sessions or days != sorted(set(days)) or self.observations[-1] != self.anchor.observations[0]:
                raise ValueError("the window must hold the requested sessions, ascending, ending at the anchor")
            if any(o.series_id != self.anchor.series_ids[0] or not known_before(o, self.anchor.cutoff)
                   for o in self.observations):
                raise ValueError("every observation must be of the series and available, in that version, strictly before the cutoff")
        return self


class AuditNote(Contract):
    """Retrospective detail about a skipped value. May contain later knowledge; never decision-facing."""

    session: date
    series_id: Text
    detail: Text
