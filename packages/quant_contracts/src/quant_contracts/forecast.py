"""Contracts for forecast tickets: what was decided after a session, written once, and scored only later.

DRAFT. A ticket holds what was known at its decision time and nothing after it. What
happened next is a separate record, written when the horizon has passed, so a ticket can
never be edited to fit its outcome.
"""

from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from quant_contracts.research import Contract, Text

GateState = Literal["GREEN", "YELLOW", "RED"]
EXPOSURE = {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0}


class BetaTicket(Contract):
    """The sector or market risk gate for one subject after one session."""

    ticket_id: Text  # "beta:<subject>:<session>"
    subject_id: Text
    session: date
    decision_time: AwareDatetime
    # When the ticket was actually written, and whether that was in time. live: after the decision and
    # before the next session opened. replay: later, so it shows what the rule would have said and is
    # not evidence of a forecast made in time. Both are None only on a ticket issued before these
    # fields existed: its issuance is unknown, and it is left as it was written.
    issued_at: AwareDatetime | None = None
    issuance: Literal["live", "replay"] | None = None
    state: GateState
    target_exposure: float
    held: bool  # True when an input was missing and the previous ticket's state was carried
    stressed_rules: tuple[Text, ...]  # the rules that fired; empty when held
    inputs: dict[Text, float | None]  # each rule's value as knowable at the decision time
    horizon_sessions: int = Field(gt=0)
    source: Text  # which frozen rule or promoted model produced it
    recipe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # hash of the registration it follows
    snapshot_sha256: dict[Text, str]

    @model_validator(mode="after")
    def exposure_follows_state(self) -> Self:
        if self.target_exposure != EXPOSURE[self.state] or self.ticket_id != f"beta:{self.subject_id}:{self.session}":
            raise ValueError("the exposure must be the state's, and the id must name the subject and session")
        if (self.issued_at is None) != (self.issuance is None):
            raise ValueError("issued_at and issuance are recorded together or not at all")
        if self.issued_at is not None and self.issued_at < self.decision_time:
            raise ValueError("a ticket cannot be issued before its decision time")
        return self


class ShadowTicket(Contract):
    """What a fitted gate said after one session, recorded beside the gate in force. It decides nothing.

    A shadow ticket vetoes no alpha ticket and moves no exposure. It exists so that a fitted
    gate has a record made in time, which a later review can compare with the rule's.
    """

    ticket_id: Text  # "shadow:<gate>:<subject>:<session>"
    gate: Text  # the registration's identifier of the fitted gate
    subject_id: Text
    session: date
    decision_time: AwareDatetime  # the information cutoff: every input was knowable strictly before it
    issued_at: AwareDatetime  # when the ticket was written, which is later than the cutoff
    issuance: Literal["live", "replay"]  # live: written before the next session opened
    machine: Text  # the computer that wrote it
    state: GateState
    target_exposure: float
    held: bool  # True when the session has no score, because an input was missing, and the previous state is carried
    score: float | None  # the mean of ``seed_scores``; None when held
    seed_scores: tuple[float, ...]  # one per fitted seed, in the registration's order; empty when held
    model_month: Text = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")  # the month whose refit scored this session
    inputs: dict[Text, float | None]  # every input as knowable at the cutoff; None where it was not
    horizon_sessions: int = Field(gt=0)
    registration_id: Text
    registration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    inputs_sha256: dict[Text, str]  # every file the inputs were built from: downloads, their records, calendars, bindings

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.target_exposure != EXPOSURE[self.state] or self.ticket_id != f"shadow:{self.gate}:{self.subject_id}:{self.session}":
            raise ValueError("the exposure must be the state's, and the id must name the gate, subject and session")
        if self.issued_at < self.decision_time:
            raise ValueError("a ticket cannot be issued before its decision time")
        if self.held != (self.score is None) or self.held != (not self.seed_scores):
            raise ValueError("a held ticket has no score and no seed scores; any other has both")
        if self.score is not None and abs(self.score - sum(self.seed_scores) / len(self.seed_scores)) > 1e-12:
            raise ValueError("the score is the mean of the seed scores")
        if self.held and any(v is None for v in self.inputs.values()) is False:
            raise ValueError("a ticket is held only when an input is missing")
        return self


class ShadowGap(Contract):
    """A session on which a fitted gate could say nothing: it had no score and no earlier live ticket to hold.

    Written in place of a ticket, so the session is on record as a gap and is never read as a state.
    """

    gap_id: Text  # "gap:<gate>:<subject>:<session>"
    gate: Text
    subject_id: Text
    session: date
    decision_time: AwareDatetime
    issued_at: AwareDatetime
    machine: Text
    reason: Text
    missing_inputs: tuple[Text, ...]
    registration_id: Text
    registration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    inputs_sha256: dict[Text, str]


class AlphaTicket(Contract):
    """A single-stock view for one subject after one session. Always subordinate to the beta gate."""

    ticket_id: Text
    subject_id: Text
    session: date
    decision_time: AwareDatetime
    direction: Literal["bullish", "bearish", "neutral"]
    horizon_sessions: int = Field(gt=0)
    recipe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_sha256: dict[Text, str]


class GatedAlpha(Contract):
    """What the beta gate lets an alpha ticket do. The ticket itself is not changed."""

    alpha_ticket_id: Text
    beta_ticket_id: Text
    action: Literal["allowed", "capped", "vetoed"]
    reason: Text


class MaturedOutcome(Contract):
    """What happened after a ticket, recorded once its horizon has passed. Never part of the ticket."""

    ticket_id: Text
    entry_session: date
    last_session: date
    forward_drawdown: float  # lowest close of the horizon over the entry open, minus one
    forward_average_return: float  # mean close of the horizon over the entry open, minus one
    snapshot_sha256: dict[Text, str]
