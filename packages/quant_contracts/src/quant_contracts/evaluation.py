"""Contracts for evaluation output: one row per event, and the table that holds them.

DRAFT. A row reports what was knowable before an event and what happened after it. It
makes no claim that one explains the other.
"""

from datetime import date
from typing import Literal

from pydantic import Field, computed_field

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


class EventOutcomeRow(Contract):
    """One event: its cutoff, the market state knowable before it, and the returns after it.

    ``prior_status`` and ``timing_verified`` are separate. The first says whether the event
    predates the frozen research map. The second says whether the cutoff and availability
    times are exact; when False the row holds under the stated timing assumptions only.
    """

    event_id: Text
    cutoff: Cutoff
    state: AsOfResult  # the latest market-state inputs knowable before the cutoff
    state_value: float | None  # the state measure computed from them; None for a gap
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
    rows: tuple[EventOutcomeRow, ...]
