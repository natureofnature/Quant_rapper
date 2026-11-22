"""Contracts for the causal-graph stage: research leads, evidence records, factor recipes.

DRAFT. These are data shapes with their invariants and nothing else: no loading, no
projection, no scoring.

Two rules, kept apart:

1. Decision inputs are point-in-time. A recipe cannot be built from evidence that was
   not yet available at its decision time. This is a hard rejection.
2. The prior is versioned, not dated. A lead is a question from a frozen revision of the
   research map and carries that revision. A recipe whose decision time is before the
   revision was frozen is ``exploratory``: its leads may have been chosen knowing what
   happened. Test such a lead on held-out or later observations.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

RelationKind = Literal["mechanism_hypothesis", "observation_lead"]
Availability = Literal["filing", "official_archive", "news_only", "members_only"]
PriorStatus = Literal["prior_available_at_decision", "exploratory"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]  # never blank


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Citation(Contract):
    """Where a fact was published and how provably it is dated."""

    url: Text
    locator: Text  # page, section or table inside the source
    published_on: date
    published_at: AwareDatetime | None = None  # in the source's publication timezone
    retrieved_on: date
    availability: Availability

    @model_validator(mode="after")
    def publication_dates_are_consistent(self) -> Self:
        if self.published_at is not None and self.published_at.date() != self.published_on:
            raise ValueError("published_at must match published_on in the source's publication timezone")
        if self.retrieved_on < self.published_on:
            raise ValueError("retrieved_on is earlier than published_on")
        return self

    @property
    def available_at(self) -> datetime:
        """When the source could first inform a decision.

        A source that states only a date counts from the moment that date has ended
        everywhere (noon UTC the next day), so it never informs a same-day decision.
        """
        if self.published_at is not None:
            return self.published_at
        return datetime.combine(self.published_on + timedelta(days=1), time(12), timezone.utc)


class LeadTestCriteria(Contract):
    """How a lead is tested. Mirrors ``test_criteria`` in the knowledge base."""

    trigger: Text
    observable: Text
    window: Text
    supports_if: Text
    contradicts_if: Text


class GraphVersion(Contract):
    """The frozen sector map and subject profile a lead was read from.

    Both are pinned because the profile is what makes a sector lead apply to the subject.
    """

    sector_id: Text
    sector_revision: Text
    sector_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: Text
    profile_revision: Text
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_at: AwareDatetime  # when this pair of revisions was fixed, e.g. its commit time


class ResearchLead(Contract):
    """One question about one subject, taken from a frozen revision of the research map.

    A lead is a hypothesis to investigate, never evidence.
    """

    lead_id: Text  # the origin edge or exposure id; evidence cites this
    subject_id: Text  # e.g. "company:tsmc"
    source: GraphVersion
    relation_kind: RelationKind
    scope: Literal["concept", "sector", "company"]
    hop: Literal["direct", "second_hop", "lateral"]
    from_id: Text
    to_id: Text
    mechanism: Text
    test_criteria: LeadTestCriteria | None = None

    @model_validator(mode="after")
    def subject_matches_profile(self) -> Self:
        if self.subject_id != self.source.profile_id:
            raise ValueError("subject_id differs from the profile the lead was read from")
        return self


class EvidenceRecord(Contract):
    """One finding about one lead or one profile observable, or a recorded gap.

    ``evidence_class``:
      direct   - the source reports the lead's own observable for the subject.
      proxy    - the source reports a related series or a different entity.
      data_gap - nothing dated was found; ``missing`` says what was looked for.
    """

    evidence_id: Text
    subject_id: Text
    lead_id: Text | None = None
    observable: Text | None = None  # a profile's observables_to_research text
    evidence_class: Literal["direct", "proxy", "data_gap"]
    assessment: Literal["supports", "contradicts", "inconclusive"] | None = None
    summary: Text
    citation: Citation | None = None
    missing: Text | None = None

    @model_validator(mode="after")
    def shape_matches_class(self) -> Self:
        if (self.lead_id is None) == (self.observable is None):
            raise ValueError("cite exactly one of lead_id or observable")
        if self.evidence_class == "data_gap":
            if self.citation is not None or self.assessment is not None or not self.missing:
                raise ValueError("a data_gap has missing, and no citation or assessment")
        elif self.citation is None or self.assessment is None or self.missing is not None:
            raise ValueError("direct and proxy evidence need a citation and an assessment, and no missing")
        return self

    @property
    def available_at(self) -> datetime | None:
        """When the source could first inform a decision; None for a data gap."""
        return self.citation.available_at if self.citation else None


class DataRequirement(Contract):
    """One input series a recipe needs, and whether it can be had."""

    series: Text  # e.g. "TSMC monthly net revenue"
    availability: Availability | None = None  # None when no source is known
    typical_lag: str | None = None
    status: Literal["observable", "proxy", "data_gap"]


class CandidateFactorRecipe(Contract):
    """What the causal graph hands to the evaluator: a testable claim, not a result.

    It states which dated event to look for, which return to measure and over which
    horizons. It contains no returns and no verdict.
    """

    recipe_id: Text
    subject_id: Text
    decision_time: AwareDatetime
    leads: tuple[ResearchLead, ...] = Field(min_length=1)  # the leads this recipe rests on
    evidence: tuple[EvidenceRecord, ...] = ()  # the evidence used to form it, if any

    trigger: Text  # the dated event that starts the window
    trigger_availability: Availability  # how provably the trigger date is known
    # "unspecified" is for discovery; directional evaluation must require a direction.
    expected_direction: Literal["positive", "negative", "unspecified"]
    benchmark: Text | None  # ticker whose return is subtracted; None when the subject's own return is measured
    horizons_sessions: tuple[int, ...] = Field(default=(5, 21), min_length=1)
    data_requirements: tuple[DataRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def inputs_are_consistent(self) -> Self:
        if any(h <= 0 for h in self.horizons_sessions):
            raise ValueError("horizons_sessions must be positive")
        lead_ids = {lead.lead_id for lead in self.leads}
        if len(lead_ids) != len(self.leads):
            raise ValueError("duplicate lead in leads")
        if any(lead.source != self.leads[0].source for lead in self.leads[1:]):
            raise ValueError("all leads must use the same frozen graph version")
        for item in (*self.leads, *self.evidence):
            if item.subject_id != self.subject_id:
                raise ValueError("a lead or evidence record is about a different subject")
        for record in self.evidence:
            if record.lead_id is not None and record.lead_id not in lead_ids:
                raise ValueError(f"evidence {record.evidence_id!r} cites a lead the recipe does not include")
            if record.available_at is not None and record.available_at > self.decision_time:
                raise ValueError(f"evidence {record.evidence_id!r} was not available at the decision time")
        return self

    @property
    def prior_frozen_at(self) -> datetime:
        """When the recipe's sector map and subject profile were frozen together."""
        return self.leads[0].source.frozen_at

    @property
    def prior_status(self) -> PriorStatus:
        """``exploratory`` when the decision predates the prior, so hindsight is possible."""
        return "prior_available_at_decision" if self.prior_frozen_at <= self.decision_time else "exploratory"

    @property
    def observability(self) -> Literal["observable", "proxy", "data_gap"]:
        """The weakest status among the inputs: one gap makes the recipe a gap."""
        statuses = {r.status for r in self.data_requirements}
        return "data_gap" if "data_gap" in statuses else "proxy" if "proxy" in statuses else "observable"
