"""Contracts for a causal map: the candidate factors worth investigating for one subject at one decision time.

DRAFT. Candidates are generated from the subject's frozen profile, the shared bindings and
the versioned factor definitions; a caller supplies no lead list and no factor list. Every
candidate remains a hypothesis. The map holds no returns and nothing learned later.
"""

from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from quant_contracts.evaluation import Factor
from quant_contracts.pit import Cutoff, Observation
from quant_contracts.research import Contract, EvidenceRecord, GraphVersion, PriorStatus, ResearchLead, Text

SUBJECT_CLOSE = "subject:close"  # in a factor definition: the closing-price series bound to the subject itself


class FactorDefinition(Contract):
    """One way of measuring one concept of the prior. A research input, versioned with the bindings.

    ``anchor`` is the concept the factor measures, or a subject id for a factor that exists
    only for that subject. Leads reach a concept anchor through the map; a subject anchor
    names its origin leads here.
    """

    factor: Factor  # series_ids may hold SUBJECT_CLOSE
    anchor: Text
    reads: Literal["direct", "proxy"]  # proxy: a named stand-in, never reported as the concept itself
    values_from: Literal["snapshot", "packet"]  # packet: values exist only as dated sources report them
    note: Text
    origin_lead_ids: tuple[Text, ...] = ()


class ResearchInputs(Contract):
    """The factor definitions and outcome settings the generator reads. Nothing here is per run."""

    schema_version: Text
    kind: Literal["factor_definitions"]
    content_revision: Text
    describe: Text
    prior_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    factors: tuple[FactorDefinition, ...]
    benchmarks: dict[Text, Text]  # subject id -> the subject whose return is subtracted; absent means none
    horizon_sessions: dict[Literal["company", "fund"], int]
    # series id -> hash of its binding, for every series a generated candidate or recipe may use.
    # A run is refused if a binding it uses is not pinned here or has changed since; bindings no
    # run uses can be added to the catalogue freely.
    bindings_sha256: dict[Text, str]

    @model_validator(mode="after")
    def factor_ids_are_unique(self) -> Self:
        ids = [d.factor.factor_id for d in self.factors]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate factor id")
        return self


class ContentValue(Contract):
    """A value a dated source reports for a bound series. It is knowable when that source is."""

    series_id: Text
    session: date  # the period the value is for, as a session of the series' calendar
    value: float
    evidence_id: Text  # the evidence record whose citation dates it


class EventFocus(Contract):
    """An event that focuses the inquiry. Its time is recorded apart from any decision time."""

    event_id: Text
    observable: Text  # the profile observable this event is a release of
    event_time: Cutoff
    content_series: Text  # the bound series carrying what the event reports
    content_session: date  # the period it reports; another period's value is not this event's content


class SourcePacket(Contract):
    """Curated dated sources about one subject. It supplies evidence; it selects no candidates.

    It may hold sources from any date: what a decision time may see is decided when the map
    is built. A recorded gap has no date and is always shown, so it must describe something
    already true at every decision time the packet is replayed at.
    """

    describe: Text  # who collected the sources, when and how
    subject_id: Text
    evidence: tuple[EvidenceRecord, ...] = ()
    content: tuple[ContentValue, ...] = ()
    event: EventFocus | None = None

    @model_validator(mode="after")
    def references_resolve(self) -> Self:
        evidence = {e.evidence_id: e for e in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("duplicate evidence id")
        if any(e.subject_id != self.subject_id for e in self.evidence):
            raise ValueError("an evidence record is about a different subject")
        for value in self.content:
            if value.evidence_id not in evidence or evidence[value.evidence_id].citation is None:
                raise ValueError(f"content for {value.session} needs an evidence record with a citation")
        return self


class Candidate(Contract):
    """One thing worth investigating, with the leads it comes from.

    measurable - a defined factor on bound series; a recipe is generated for it.
    unmeasured - a relevant hypothesis with no usable input now; ``missing`` names the observation.
    The counts say what the available evidence did, not what is true.
    """

    candidate_id: Text
    status: Literal["measurable", "unmeasured"]
    origin_lead_ids: tuple[Text, ...] = Field(min_length=1)
    relevance: Text  # why it bears on this subject, generated from the prior
    via: tuple[Text, ...] = ()  # concepts linking the origin leads to what the factor measures
    factor: Factor | None = None
    reads: Literal["direct", "proxy"] | None = None
    # When the factor is read and the return window starts, for a candidate with a factor.
    #   decision_time - a state of the market: read before the decision time, entry after it.
    #   event_content - what the event reports: usable only once the event's own figure is
    #                   eligible, read for the event's period, entry after the decision time.
    # Neither is "the reaction to the event given the state before it"; that question needs
    # entry after the event's own time and is not generated here.
    timing: Literal["decision_time", "event_content"] | None = None
    recipe_id: Text | None = None  # the recipe generated for it; set exactly when measurable
    # Hash of that recipe as generated. The id names subject, factor, benchmark and horizon and
    # so repeats across maps; the hash also covers the trigger, leads, evidence and decision time.
    recipe_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    missing: Text | None = None
    evidence_ids: tuple[Text, ...] = ()
    supports: int = Field(ge=0)
    contradicts: int = Field(ge=0)
    inconclusive: int = Field(ge=0)
    gaps: int = Field(ge=0)

    @model_validator(mode="after")
    def shape_matches_status(self) -> Self:
        if (self.factor is None) != (self.timing is None):
            raise ValueError("a candidate with a factor states its timing, and one without has none")
        if self.status == "measurable" and (None in (self.factor, self.reads, self.recipe_id, self.recipe_sha256)
                                            or self.missing is not None):
            raise ValueError("a measurable candidate has a factor and a recipe with its hash, and nothing missing")
        if self.status == "unmeasured" and (self.missing is None or self.recipe_id is not None or self.recipe_sha256 is not None):
            raise ValueError("an unmeasured candidate names what is missing and has no recipe")
        return self


class CausalMap(Contract):
    """What one decision may see for one subject. Safe to show to a step acting at ``decision_time``."""

    subject_id: Text
    decision_time: AwareDatetime
    event: EventFocus | None = None
    prior: GraphVersion
    prior_status: PriorStatus
    leads: tuple[ResearchLead, ...] = Field(min_length=1)  # every lead the profile projects
    candidates: tuple[Candidate, ...]  # every lead is an origin of at least one
    evidence: tuple[EvidenceRecord, ...] = ()  # available strictly before the decision time, plus recorded gaps
    # Reported values available by then. One dated by a source that states only a day carries an
    # assumed availability: the cutoff is a convention, not a record.
    content: tuple[Observation, ...] = ()
    inputs_sha256: dict[str, str]

    @model_validator(mode="after")
    def holds_only_what_was_available(self) -> Self:
        if any(e.available_at is not None and e.available_at >= self.decision_time for e in self.evidence):
            raise ValueError("every dated evidence record must be available strictly before the decision time")
        if any(o.available_at >= self.decision_time for o in self.content):
            raise ValueError("every content value must be available strictly before the decision time")
        if self.prior_status != ("prior_available_at_decision" if self.prior.frozen_at <= self.decision_time else "exploratory"):
            raise ValueError("prior_status does not follow from the prior and the decision time")
        lead_ids = [lead.lead_id for lead in self.leads]
        if any(lead.subject_id != self.subject_id or lead.source != self.prior for lead in self.leads):
            raise ValueError("a lead is about another subject or another revision of the prior")
        if any(e.lead_id is not None and e.lead_id not in lead_ids for e in self.evidence):
            raise ValueError("evidence cites a lead the profile does not project")
        ids = [c.candidate_id for c in self.candidates]
        origins = {i for c in self.candidates for i in c.origin_lead_ids}
        if len(set(ids)) != len(ids) or origins != set(lead_ids):
            raise ValueError("candidate ids must be unique and every lead must be the origin of a candidate")
        for candidate in self.candidates:
            records = [e for e in self.evidence if e.lead_id in candidate.origin_lead_ids]
            tally = tuple(sum(1 for r in records if r.assessment == a) for a in ("supports", "contradicts", "inconclusive"))
            gaps = sum(1 for r in records if r.evidence_class == "data_gap")
            if (candidate.supports, candidate.contradicts, candidate.inconclusive, candidate.gaps) != (*tally, gaps) \
                    or candidate.evidence_ids != tuple(r.evidence_id for r in records):
                raise ValueError(f"candidate {candidate.candidate_id!r} does not match the evidence in the map")
        return self
