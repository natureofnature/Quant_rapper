"""Contracts for an event map: the part of the research prior that bears on one event at one decision time.

DRAFT. A source packet is the curated input for one event. An event map is what a
decision at one moment may see of it: the selected leads, the evidence already available,
and the gaps. Its edges remain hypotheses. It holds no returns and nothing learned later.
"""

from datetime import date
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from quant_contracts.pit import Cutoff, Observation
from quant_contracts.research import (Availability, Contract, EvidenceRecord, GraphVersion, PriorStatus, ResearchLead,
                                      Text)


class PathChoice(Contract):
    """One lead judged relevant to the event, and why, in one sentence."""

    lead_id: Text
    relevance: Text


class ContentValue(Contract):
    """A value the event's own source reports, for a bound series. It is knowable when that source is."""

    series_id: Text
    session: date  # the period the value is for, as a session of the series' calendar
    value: float
    evidence_id: Text  # the evidence record whose citation dates it


class RecipeSpec(Contract):
    """What the recipe built from this event should measure."""

    recipe_id: Text
    trigger: Text
    trigger_availability: Availability
    benchmark: Text
    horizon_sessions: int = Field(gt=0)
    series_ids: tuple[Text, ...] = Field(min_length=1)  # bound inputs that exist whatever the decision time
    content_series: Text  # the bound series carrying the event's own content
    content_session: date  # the period this event reports


class SourcePacket(Contract):
    """The curated input for one event: which leads matter, and every dated source collected for it.

    It may hold sources from any date. What a given decision time may see is decided when
    the map is built, never here. A recorded gap has no date and is always shown, so it must
    describe something already true at every decision time the packet is replayed at.
    """

    describe: Text  # who collected the sources, when and how
    event_id: Text
    subject_id: Text
    sector_folder: Text
    symbol: Text
    observable: Text  # the profile observable this event is a release of
    prior_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    # Hash of the series bindings this packet uses, as quant_causal_graph.event_map.bindings_digest
    # computes it. The map is not built if the catalogue's entries for them have changed.
    bindings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_time: Cutoff  # when the event became public, with its basis; separate from any decision time
    paths: tuple[PathChoice, ...] = Field(min_length=1)
    evidence: tuple[EvidenceRecord, ...] = ()
    content: tuple[ContentValue, ...] = ()
    recipe: RecipeSpec

    @model_validator(mode="after")
    def references_resolve(self) -> Self:
        lead_ids = [p.lead_id for p in self.paths]
        evidence = {e.evidence_id: e for e in self.evidence}
        if len(set(lead_ids)) != len(lead_ids) or len(evidence) != len(self.evidence):
            raise ValueError("duplicate lead or evidence id")
        for record in self.evidence:
            if record.subject_id != self.subject_id:
                raise ValueError(f"evidence {record.evidence_id!r} is about a different subject")
            if record.lead_id is not None and record.lead_id not in lead_ids:
                raise ValueError(f"evidence {record.evidence_id!r} cites a lead that is not a chosen path")
        for value in self.content:
            if value.evidence_id not in evidence or evidence[value.evidence_id].citation is None:
                raise ValueError(f"content for {value.session} needs an evidence record with a citation")
        return self


class MapEdge(Contract):
    """One selected lead as it stands at the decision time. Still a hypothesis.

    The counts say what the available evidence did, not what is true: an edge with no
    evidence is an open question, and supporting and contradicting records are both kept.
    """

    lead: ResearchLead
    relevance: Text
    evidence_ids: tuple[Text, ...] = ()
    supports: int = Field(ge=0)
    contradicts: int = Field(ge=0)
    inconclusive: int = Field(ge=0)
    gaps: int = Field(ge=0)


class EventMap(Contract):
    """What one decision may see of one event. Safe to show to a step acting at ``decision_time``."""

    event_id: Text
    subject_id: Text
    observable: Text
    decision_time: AwareDatetime
    event_time: Cutoff
    prior: GraphVersion
    prior_status: PriorStatus
    edges: tuple[MapEdge, ...] = Field(min_length=1)
    evidence: tuple[EvidenceRecord, ...] = ()  # available strictly before the decision time, plus recorded gaps
    content_series: Text  # the bound series carrying what the event reports
    content_session: date  # the period this event reports; a value for another period is not this event's content
    # The reported values that were available by then. A value dated by a source that states
    # only a day carries an assumed availability: the cutoff is a convention, not a record.
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
        by_lead: dict[str, list[EvidenceRecord]] = {}
        for record in self.evidence:
            if record.lead_id is not None:
                by_lead.setdefault(record.lead_id, []).append(record)
        if set(by_lead) - {e.lead.lead_id for e in self.edges}:
            raise ValueError("evidence cites a lead that is not an edge of the map")
        for edge in self.edges:
            if edge.lead.subject_id != self.subject_id or edge.lead.source != self.prior:
                raise ValueError("an edge is about another subject or another revision of the prior")
            records = by_lead.get(edge.lead.lead_id, [])
            tally = tuple(sum(1 for r in records if r.assessment == a) for a in ("supports", "contradicts", "inconclusive"))
            gaps = sum(1 for r in records if r.evidence_class == "data_gap")
            if (edge.supports, edge.contradicts, edge.inconclusive, edge.gaps) != (*tally, gaps) \
                    or edge.evidence_ids != tuple(r.evidence_id for r in records):
                raise ValueError(f"edge {edge.lead.lead_id!r} does not match the evidence in the map")
        return self

    @property
    def nodes(self) -> tuple[str, ...]:
        """Every concept, sector or company an edge touches, in first-seen order."""
        return tuple(dict.fromkeys(i for e in self.edges for i in (e.lead.from_id, e.lead.to_id)))
