"""Exact monetary values and immutable provider request receipts."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.usage.domain.errors import UsageDomainError


MONEY_QUANTUM = Decimal("0.000000001")


def money(value: Decimal | int | str | float) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Usage money must be finite and nonnegative")
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)


class MeteringIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_id: UUID
    organization_id: UUID | None = None
    user_id: UUID
    pod_id: UUID | None = None
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    parent_agent_run_id: UUID | None = None
    source_type: str = "agent_run"
    source_id: str | None = None
    profile_id: str
    profile_scope: str
    model_name: str
    provider_model_name: str


#: Runs that answer people outside the organization: a contact in a private
#: chat, or a group's people from outside the pod. They spend the
#: organization's budget, never the personal allowance of the member who looks
#: after the conversation, and count towards the organization's contacts cap.
CONTACT_RUN = "contact_run"
OUTSIDER_RUN = "outsider_run"
OUTSIDE_AUDIENCE_SOURCES: tuple[str, ...] = (CONTACT_RUN, OUTSIDER_RUN)


class TokenCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    input_audio_tokens: int = Field(default=0, ge=0)
    output_audio_tokens: int = Field(default=0, ge=0)
    cache_audio_read_tokens: int = Field(default=0, ge=0)
    unpriced_requests: int = Field(default=0, ge=0)
    unconfirmed_requests: int = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0)


#: Bumped when what a window sums changes in code -- which rows count, or how a
#: row's cost is read -- so every counter is recounted under the new rule.
_LEDGER_BASIS_VERSION = "1"


class BudgetWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: UUID | None
    user_id: UUID | None
    kind: str
    start: datetime
    end: datetime
    limit: Decimal | None = Field(default=None, ge=0)
    excluded_organization_ids: tuple[UUID, ...] = ()
    #: Counts only usage recorded under these sources; empty counts all of it.
    source_types: tuple[str, ...] = ()
    #: Leaves out usage recorded under these sources.
    excluded_source_types: tuple[str, ...] = ()

    @property
    def basis(self) -> str:
        """Which ledger rows this window counts, as a digest.

        A counter's running total is only good for the rows it was counted
        over. The same counter row -- one organization, member, kind and start
        -- can be asked to count different rows when policy moves: a member
        joins a team, and the team's organization leaves their personal
        allowance, or a source is carved out. The counter keeps the digest it
        was last counted under, and a window that asks with another one has it
        recounted from the ledger first. The limit is not part of it: what was
        spent does not depend on what may be.
        """
        described = [
            _LEDGER_BASIS_VERSION,
            self.kind,
            self.start.astimezone(timezone.utc).isoformat(),
            self.end.astimezone(timezone.utc).isoformat(),
            str(self.organization_id),
            str(self.user_id),
            sorted(self.source_types),
            sorted(self.excluded_source_types),
            sorted(
                str(organization) for organization in self.excluded_organization_ids
            ),
        ]
        return hashlib.sha256(json.dumps(described).encode()).hexdigest()


#: The run-context metadata key a harness names its run's place in its
#: conversation under; ``MeteredModel`` reads it back for the receipt.
RUN_INDEX_METADATA_KEY = "lemma_run_index"


class RequestPosition(BaseModel):
    """Where a model request sits: which run of its conversation, which step of
    its run. What a prompt-cache hit ratio is read against -- the first request
    of a later run re-reads what the previous run cached, a later step re-reads
    its own run."""

    model_config = ConfigDict(frozen=True)

    #: 1 for a conversation's first run.
    run_index: int | None = Field(default=None, ge=1)
    #: pydantic-ai's ``run_step``: 1 for a run's first model request.
    step_index: int | None = Field(default=None, ge=0)


class RequestReceipt(BaseModel):
    """One immutable provider outcome, replayable after a lost commit response."""

    model_config = ConfigDict(frozen=True)

    request_id: UUID
    counts: TokenCounts
    cost: Decimal | None = Field(default=None, ge=0)
    occurred_at: datetime
    position: RequestPosition | None = None

    def cache_report(self) -> dict[str, float | int | None]:
        """The request's prompt-cache use, as its usage record states it."""
        counts = self.counts
        return {
            "input_tokens": counts.input_tokens,
            "cache_read_tokens": counts.cache_read_tokens,
            "cache_write_tokens": counts.cache_write_tokens,
            # pydantic-ai's input count includes cache reads and writes on
            # every provider, so this is the share read back.
            "hit_ratio": (
                round(counts.cache_read_tokens / counts.input_tokens, 4)
                if counts.input_tokens
                else None
            ),
            "run_index": self.position.run_index if self.position else None,
            "step_index": self.position.step_index if self.position else None,
        }


class AccountingConflictError(UsageDomainError):
    """A request receipt was replayed with conflicting content."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            "Usage accounting could not safely authorize or settle this request. Please contact your workspace administrator.",
            code="USAGE_ACCOUNTING_CONFLICT",
            status_code=409,
        )
        self.details = {"reason": reason}
