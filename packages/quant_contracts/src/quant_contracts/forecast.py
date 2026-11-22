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
